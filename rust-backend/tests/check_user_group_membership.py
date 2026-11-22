# ruff: noqa: INP001
"""Verify one user's group membership through Django and an isolated Rust pilot."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
from base64 import b64encode
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import django
from check_user_group_details_direct import verify_method_map
from django.conf import settings
from django.db import connection
from psycopg2.extensions import make_dsn
from read_proxy_fixture import (
    ReadCase,
    ReadResult,
    compare_read_headers,
    exercise_paired_read_routes,
    private_read_proxy,
    validate_fixture,
    validate_local_root,
    verify_private_database,
)

from zerver.lib.test_rust_http import request_test_http, rust_http_service

if TYPE_CHECKING:
    from zerver.models import UserProfile

ROOT = Path(__file__).resolve().parents[2]
PILOT = "ZULIP_RUST_USER_GROUP_MEMBERSHIP_READ_PILOT"
MAP = "user-group-membership-map.conf"
ROUTES = "user-group-membership-routes.conf"


@dataclass(frozen=True)
class MembershipCase(ReadCase):
    expected: bool | str | None


def make_cases(cleanup: ExitStack) -> list[MembershipCase]:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.models import NamedUserGroup, Realm, UserGroupMembership, UserProfile
    from zerver.models.groups import SystemGroups

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = UserProfile.objects.get(realm=member.realm, delivery_email="polonius@zulip.com")
    nested = UserProfile.objects.get(realm=member.realm, delivery_email="shiva@zulip.com")
    direct = UserProfile.objects.get(realm=member.realm, delivery_email="othello@zulip.com")
    bot = UserProfile.objects.get(realm=member.realm, delivery_email="default-bot@zulip.com")
    root = NamedUserGroup.objects.get(realm=member.realm, name="Detail root")
    retired = NamedUserGroup.objects.get(realm=member.realm, name="Detail retired")
    outside = NamedUserGroup.objects.get(realm__string_id="zephyr", name="Detail outside")
    other = outside.direct_members.get()
    inactive = root.direct_members.get(is_active=False)
    owners = NamedUserGroup.objects.get(
        realm=member.realm, name=SystemGroups.OWNERS, is_system_group=True
    )
    assert member.role == UserProfile.ROLE_MEMBER and guest.is_guest and bot.is_bot

    # The normal existing rule allows a non-guest to see users in this
    # synthetic realm even with a restrictive guest visibility setting.
    own_realm = Realm.objects.filter(id=member.realm_id)
    cleanup.callback(
        own_realm.update, can_access_all_users_group_id=member.realm.can_access_all_users_group_id
    )
    own_realm.update(can_access_all_users_group=owners)
    bot_membership = UserGroupMembership.objects.create(user_group=root, user_profile=bot)
    cleanup.callback(bot_membership.delete)

    sessions: dict[int, str] = {}

    def auth(mode: str, user: UserProfile) -> dict[str, str]:
        if mode == "basic":
            token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
            return {"Authorization": f"Basic {token}"}
        if user.id not in sessions:
            session = SessionStore()
            session.update(
                {
                    "_auth_user_id": str(user.id),
                    "_auth_user_backend": "zproject.backends.DevAuthBackend",
                    "_auth_user_hash": user.get_session_auth_hash(),
                }
            )
            session.create()
            cleanup.callback(session.delete)
            assert session.session_key is not None
            sessions[user.id] = session.session_key
        return {"Cookie": f"{settings.SESSION_COOKIE_NAME}={sessions[user.id]}"}

    def case(
        mode: str,
        label: str,
        actor: UserProfile,
        group: int | str,
        target: int | str,
        expected: bool | str | None,
        *,
        query: str = "",
        method: str = "GET",
        status: int | None = None,
        anonymous: bool = False,
        language: str | None = None,
    ) -> MembershipCase:
        prefix = "/api/v1" if mode == "basic" else "/json"
        headers = {} if anonymous else auth(mode, actor)
        if language is not None:
            headers["Accept-Language"] = language
        host = actor.realm.host
        matched = host == member.realm.host and method != "OPTIONS"
        return MembershipCase(
            f"{mode} {label}",
            method,
            host,
            headers,
            f"{prefix}/user_groups/{group}/members/{target}{query}",
            status if status is not None else 200 if isinstance(expected, bool) else 400,
            expected,
            mapped=matched,
            also_compare_direct=not matched and method == "GET",
        )

    modes = ("basic", "session")
    reads = [case(mode, "nested", member, root.id, nested.id, True) for mode in modes]
    cases = [*reads, *(replace(read, label=f"{read.label} HEAD", method="HEAD") for read in reads)]
    for mode in modes:
        cases.extend(
            (
                case(mode, "direct target", member, root.id, direct.id, True),
                case(mode, "guest target", member, root.id, guest.id, False),
                case(mode, "bot target", member, root.id, bot.id, True),
                case(
                    mode,
                    "direct only",
                    member,
                    root.id,
                    nested.id,
                    False,
                    query="?direct_member_only=true",
                ),
                case(
                    mode,
                    "last duplicate",
                    member,
                    root.id,
                    nested.id,
                    True,
                    query="?extra_a=1&direct_member_only=true&direct_member_only=false&extra_z=2",
                ),
                case(
                    mode,
                    "malformed boolean",
                    member,
                    root.id,
                    direct.id,
                    "direct_member_only is not valid JSON",
                    query="?direct_member_only=undefined",
                ),
                case(
                    mode,
                    "nonboolean",
                    member,
                    root.id,
                    direct.id,
                    "direct_member_only is not a boolean",
                    query="?direct_member_only=1",
                ),
                case(
                    mode,
                    "guest first",
                    guest,
                    0,
                    inactive.id,
                    "Not allowed for guest users",
                    query="?direct_member_only=undefined",
                ),
                case(mode, "retired first", member, retired.id, 0, "User group is deactivated."),
                case(mode, "missing group first", member, outside.id, 0, "Invalid user group"),
                case(mode, "target outside realm", member, root.id, other.id, "No such user"),
                case(mode, "target inactive", member, root.id, inactive.id, "User is deactivated"),
                case(
                    mode,
                    "target inactive French",
                    member,
                    root.id,
                    inactive.id,
                    None,
                    language="fr",
                ),
                case(
                    mode,
                    "target inactive HEAD",
                    member,
                    root.id,
                    inactive.id,
                    "User is deactivated",
                    method="HEAD",
                ),
                case(mode, "large group ID", member, 2**63, nested.id, "Invalid user group"),
                case(mode, "large target ID", member, root.id, 2**31, "No such user"),
                case(mode, "leading zeros", member, f"0{root.id}", f"00{direct.id}", True),
                case(mode, "another realm", other, outside.id, other.id, True),
                case(
                    mode, "anonymous", member, root.id, direct.id, None, status=401, anonymous=True
                ),
                case(
                    mode,
                    "Django OPTIONS",
                    member,
                    root.id,
                    direct.id,
                    None,
                    method="OPTIONS",
                    status=204,
                    anonymous=True,
                ),
            )
        )
    cases.append(case("basic", "bot actor", bot, root.id, direct.id, True))
    return cases


def validate(case: MembershipCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method in {"HEAD", "OPTIONS"}:
        assert result[1] == b"", case.label
    elif isinstance(case.expected, bool):
        assert json.loads(result[1])["is_user_group_member"] is case.expected, case.label
    elif case.expected is not None:
        assert json.loads(result[1])["msg"] == case.expected, case.label


def compare(case: MembershipCase, actual: ReadResult, expected: ReadResult) -> None:
    validate(case, actual)
    if case.method == "GET":
        assert json.loads(actual[1]) == json.loads(expected[1]), (
            case.label,
            json.loads(actual[1]),
            json.loads(expected[1]),
        )
    else:
        assert actual[1] == expected[1], case.label
    compare_read_headers(actual, expected, case.label)


def startup_guards() -> None:
    db = connection.settings_dict
    assert not db["PASSWORD"]
    dsn = make_dsn(dbname=db["NAME"], host=db["HOST"], user=db["USER"], port=db["PORT"])
    for extra, error in (
        ({}, "User-group membership read pilot requires ZULIP_RUST_DATABASE_URL"),
        (
            {"ZULIP_RUST_DATABASE_URL": dsn, "ZULIP_RUST_EXTERNAL_HOST": settings.EXTERNAL_HOST},
            "User-group membership read pilot requires signed Django sessions",
        ),
    ):
        result = subprocess.run(
            [os.environ["ZULIP_RUST_HTTP_BIN"], "127.0.0.1:0"],
            env={PILOT: "1", **extra},
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        assert result.returncode != 0 and error in result.stderr


def ensure_default_off(stage: Path, cases: list[MembershipCase]) -> None:
    secret = stage / "signed-session-input"
    secret.write_text(settings.SECRET_KEY)
    secret.chmod(0o600)
    sessions = {
        "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
        "ZULIP_RUST_DJANGO_AUTH_BACKENDS": json.dumps(settings.AUTHENTICATION_BACKENDS),
        "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
        "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
    }
    for extra in ({}, {"ZULIP_RUST_USER_GROUP_DETAIL_READ_PILOT": "1"}, {PILOT: "1"}):
        with rust_http_service({**sessions, **extra}) as port:
            for case in cases[:2]:
                path = case.path.rsplit("/", 1)[0] if extra.get(PILOT) else case.path
                result = request_test_http(port, case.method, case.host, case.headers, path)
                assert result[0] == 404, case.label


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    for tool in ("nginx", "uwsgi", "venv", "openssl"):
        parser.add_argument(f"--{tool}", type=Path, required=True)
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("this pilot requires a private loopback-only network namespace")
    if any(name.startswith("ZULIP_RUST_") and name != "ZULIP_RUST_HTTP_BIN" for name in os.environ):
        raise RuntimeError("use a scrubbed environment with only ZULIP_RUST_HTTP_BIN configured")
    validate_fixture(root)
    django.setup()
    verify_private_database()
    startup_guards()

    with tempfile.TemporaryDirectory(prefix="membership-", dir=root) as tmp, ExitStack() as cleanup:
        stage = Path(tmp)
        cases = make_cases(cleanup)
        ensure_default_off(stage, cases)
        verify_method_map(
            args.nginx,
            stage / "methods",
            tuple(cases[:2]),
            map_filename=MAP,
            map_variable="zulip_rust_user_group_membership",
        )
        with private_read_proxy(
            ROOT, stage, args.nginx, args.uwsgi, args.venv, (MAP,), openssl_bin=args.openssl
        ) as proxy:
            exercise_paired_read_routes(
                proxy,
                stage,
                ROUTES,
                cases,
                validate=validate,
                compare=compare,
                rust_extra_env={PILOT: "1"},
            )


if __name__ == "__main__":
    main()
