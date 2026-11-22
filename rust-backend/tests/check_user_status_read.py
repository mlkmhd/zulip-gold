# ruff: noqa: INP001
"""Rehearse read-only Basic and session user status in a private TLS proxy."""

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
from django.utils.timezone import now
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
PILOT = "ZULIP_RUST_USER_STATUS_READ_PILOT"
MAP = "user-status-map.conf"
ROUTES = "user-status-routes.conf"


@dataclass(frozen=True)
class StatusCase(ReadCase):
    payload: dict[str, str | bool] | None
    message: str | None


def make_cases(cleanup: ExitStack) -> list[StatusCase]:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.lib.streams import create_stream_if_needed
    from zerver.models import NamedUserGroup, Subscription, UserProfile, UserStatus
    from zerver.models.clients import get_client
    from zerver.models.groups import SystemGroups
    from zerver.models.recipients import get_or_create_direct_message_group

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = UserProfile.objects.get(realm=member.realm, delivery_email="polonius@zulip.com")
    shared_dm = UserProfile.objects.get(realm=member.realm, delivery_email="prospero@zulip.com")
    hidden = UserProfile.objects.get(realm=member.realm, delivery_email="othello@zulip.com")
    unsubscribed = UserProfile.objects.get(
        realm=member.realm, delivery_email__iexact="aaron@zulip.com"
    )
    admin = UserProfile.objects.get(realm=member.realm, delivery_email="iago@zulip.com")
    inactive = UserProfile.objects.get(realm=member.realm, delivery_email="cordelia@zulip.com")
    no_status = UserProfile.objects.get(realm=member.realm, delivery_email__iexact="zoe@zulip.com")
    bot = UserProfile.objects.get(realm=member.realm, delivery_email="default-bot@zulip.com")
    foreign = list(
        UserProfile.objects.filter(realm__string_id="zephyr", is_active=True, is_bot=False)
        .select_related("realm")
        .order_by("id")[:2]
    )
    assert len(foreign) == 2 and guest.is_guest and admin.is_realm_admin and bot.is_bot
    foreign_guest, foreign_target = foreign
    foreign_guest.role = UserProfile.ROLE_GUEST
    foreign_guest.save(update_fields=["role"])
    assert (
        foreign_target.realm.can_access_all_users_group_id
        == NamedUserGroup.objects.get(
            realm_for_sharding=foreign_target.realm,
            is_system_group=True,
            name=SystemGroups.EVERYONE,
        ).id
    )

    members = NamedUserGroup.objects.get(
        realm_for_sharding=member.realm, is_system_group=True, name=SystemGroups.MEMBERS
    )
    member.realm.can_access_all_users_group = members
    member.realm.save(update_fields=["can_access_all_users_group"])
    Subscription.objects.filter(
        user_profile_id__in=(guest.id, foreign_guest.id), active=True
    ).update(active=False)
    stream, _ = create_stream_if_needed(member.realm, "Pilot shared status")
    assert stream.recipient_id is not None
    Subscription.objects.bulk_create(
        [
            Subscription(
                recipient_id=stream.recipient_id,
                user_profile=user,
                is_user_active=True,
                active=user != unsubscribed,
            )
            for user in (guest, member, unsubscribed)
        ]
    )
    get_or_create_direct_message_group([guest.id, shared_dm.id])
    UserProfile.objects.filter(id=inactive.id).update(is_active=False)
    UserProfile.objects.filter(id__in=[member.id, no_status.id]).update(presence_enabled=False)

    client = get_client("website")

    def set_status(user: UserProfile, text: str, emoji: str = "", code: str = "") -> None:
        UserStatus.objects.create(
            user_profile=user,
            client=client,
            timestamp=now(),
            status_text=text,
            emoji_name=emoji,
            emoji_code=code,
        )

    set_status(member, "Working ☀", "car", "1f697")
    set_status(guest, "On a break")
    set_status(shared_dm, "In a meeting")
    set_status(hidden, "On vacation")
    set_status(unsubscribed, "Away")
    set_status(foreign_target, "Other organization")
    set_status(admin, "")
    with_emoji = {
        "away": True,
        "status_text": "Working ☀",
        "emoji_name": "car",
        "emoji_code": "1f697",
        "reaction_type": UserStatus.UNICODE_EMOJI,
    }
    sessions: dict[int, str] = {}

    def authenticate(mode: str, user: UserProfile) -> dict[str, str]:
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
        target: int | str,
        payload: dict[str, str | bool] | None = None,
        *,
        error: str | None = None,
        status: int | None = None,
        method: str = "GET",
        suffix: str = "",
        language: str | None = None,
        anonymous: bool = False,
    ) -> StatusCase:
        prefix = "/api/v1" if mode == "basic" else "/json"
        headers = {} if anonymous else authenticate(mode, actor)
        if language:
            headers["Accept-Language"] = language
        path = f"{prefix}/users/{target}/status{suffix}"
        mapped = actor.realm == member.realm and method != "OPTIONS" and target != "me"
        return StatusCase(
            f"{mode} {label}",
            method,
            actor.realm.host,
            headers,
            path,
            status if status is not None else 200 if payload is not None else 400,
            payload,
            error,
            mapped=mapped,
            also_compare_direct=not mapped and actor.realm != member.realm and method == "GET",
        )

    modes = ("basic", "session")
    reads = [case(mode, "member self", member, member.id, with_emoji) for mode in modes]
    cases = [*reads, *(replace(read, label=f"{read.label} HEAD", method="HEAD") for read in reads)]
    for mode in modes:
        cases.extend(
            (
                case(mode, "guest self", guest, guest.id, {"status_text": "On a break"}),
                case(mode, "guest shared channel", guest, member.id, with_emoji),
                case(mode, "guest shared DM", guest, shared_dm.id, {"status_text": "In a meeting"}),
                case(mode, "guest restricted", guest, hidden.id, error="Insufficient permission"),
                case(
                    mode,
                    "inactive subscription",
                    guest,
                    unsubscribed.id,
                    error="Insufficient permission",
                ),
                case(
                    mode, "admin reads same realm", admin, hidden.id, {"status_text": "On vacation"}
                ),
                case(
                    mode,
                    "member reads restricted realm",
                    member,
                    hidden.id,
                    {"status_text": "On vacation"},
                ),
                case(mode, "absent status while away", member, no_status.id, {}),
                case(mode, "empty status row", member, admin.id, {}),
                case(mode, "inactive target", guest, inactive.id, error="User is deactivated"),
                case(mode, "bot target", guest, bot.id, error="No such user"),
                case(mode, "cross realm target", member, foreign_target.id, error="No such user"),
                case(
                    mode,
                    "other realm everyone",
                    foreign_guest,
                    foreign_target.id,
                    {"status_text": "Other organization"},
                ),
                case(mode, "missing target", member, 9999999, error="No such user"),
                case(mode, "large missing target", member, 10**22, error="No such user"),
                case(mode, "missing target French", member, 9999999, language="fr"),
                case(mode, "restricted HEAD", guest, hidden.id, method="HEAD"),
                case(
                    mode,
                    "untyped query and leading zero",
                    member,
                    f"00{member.id}",
                    with_emoji,
                    suffix="?client=website&unused=1",
                ),
                case(mode, "no authorization", member, member.id, status=401, anonymous=True),
                case(
                    mode,
                    "Django OPTIONS",
                    member,
                    member.id,
                    status=204,
                    method="OPTIONS",
                    anonymous=True,
                ),
                case(
                    mode, "self write path remains Django", member, "me", status=405, method="HEAD"
                ),
            )
        )
    cases.append(case("basic", "bot caller sees member", bot, member.id, with_emoji))
    return cases


def validate(case: StatusCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method in {"HEAD", "OPTIONS"}:
        assert result[1] == b"", case.label
    elif case.payload is not None:
        assert json.loads(result[1])["status"] == case.payload, case.label
    elif case.message is not None:
        assert json.loads(result[1])["msg"] == case.message, case.label


def compare(case: StatusCase, actual: ReadResult, expected: ReadResult) -> None:
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


def startup_guards(stage: Path, reads: list[StatusCase]) -> None:
    db = connection.settings_dict
    assert not db["PASSWORD"]
    dsn = make_dsn(dbname=db["NAME"], host=db["HOST"], user=db["USER"], port=db["PORT"])
    for extra, error in (
        ({}, "User status read pilot requires ZULIP_RUST_DATABASE_URL"),
        (
            {"ZULIP_RUST_DATABASE_URL": dsn, "ZULIP_RUST_EXTERNAL_HOST": settings.EXTERNAL_HOST},
            "User status read pilot requires signed Django sessions",
        ),
    ):
        process = subprocess.run(
            [os.environ["ZULIP_RUST_HTTP_BIN"], "127.0.0.1:0"],
            env={PILOT: "1", **extra},
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        assert process.returncode != 0 and error in process.stderr
    secret = stage / "synthetic-signing-input"
    secret.write_text(settings.SECRET_KEY)
    secret.chmod(0o600)
    with rust_http_service(
        {
            "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
            "ZULIP_RUST_DJANGO_AUTH_BACKENDS": json.dumps(settings.AUTHENTICATION_BACKENDS),
            "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
            "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
        }
    ) as port:
        for case in reads:
            result = request_test_http(port, "GET", case.host, case.headers, case.path)
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
    with (
        tempfile.TemporaryDirectory(prefix="user-status-", dir=root) as tmp,
        ExitStack() as cleanup,
    ):
        stage = Path(tmp)
        cases = make_cases(cleanup)
        startup_guards(stage, cases[:2])
        verify_method_map(
            args.nginx,
            stage / "methods",
            tuple(cases[:2]),
            map_filename=MAP,
            map_variable="zulip_rust_user_status",
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
