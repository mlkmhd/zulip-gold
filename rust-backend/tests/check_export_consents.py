# ruff: noqa: INP001
"""Rehearse read-only Basic and session export consents in a private TLS proxy."""

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
PILOT = "ZULIP_RUST_EXPORT_CONSENTS_READ_PILOT"
MAP = "export-consents-map.conf"
ROUTES = "export-consents-routes.conf"


@dataclass(frozen=True)
class ConsentCase(ReadCase):
    flags: dict[int, tuple[bool, int]] | None
    absent: frozenset[int]
    message: str | None


def make_cases(cleanup: ExitStack) -> list[ConsentCase]:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.models import UserProfile

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")

    def user(name: str) -> UserProfile:
        return UserProfile.objects.get(realm=member.realm, delivery_email=f"{name}@zulip.com")

    admin = user("iago")
    owner = user("othello")
    moderator = user("prospero")
    guest = user("polonius")
    inactive = user("cordelia")
    bot = user("default-bot")
    foreign = UserProfile.objects.filter(
        realm__string_id="zephyr", role=UserProfile.ROLE_MEMBER, is_active=True, is_bot=False
    ).first()
    assert foreign is not None and guest.is_guest and bot.is_bot
    for profile, role in (
        (admin, UserProfile.ROLE_REALM_ADMINISTRATOR),
        (owner, UserProfile.ROLE_REALM_OWNER),
        (moderator, UserProfile.ROLE_MODERATOR),
        (foreign, UserProfile.ROLE_REALM_ADMINISTRATOR),
    ):
        UserProfile.objects.filter(id=profile.id).update(role=role)

    privacy = UserProfile.EMAIL_ADDRESS_VISIBILITY_NOBODY
    admins = UserProfile.EMAIL_ADDRESS_VISIBILITY_ADMINS
    restricted = UserProfile.EMAIL_ADDRESS_VISIBILITY_MODERATORS
    for profile, opted_in, visibility in (
        (member, True, privacy),
        (owner, False, admins),
        (guest, True, admins),
        (moderator, False, restricted),
        (inactive, True, privacy),
        (bot, True, privacy),
        (foreign, True, restricted),
    ):
        UserProfile.objects.filter(id=profile.id).update(
            allow_private_data_export=opted_in, email_address_visibility=visibility
        )
    UserProfile.objects.filter(id=inactive.id).update(is_active=False)
    flags = {
        member.id: (True, privacy),
        owner.id: (False, admins),
        guest.id: (True, admins),
        moderator.id: (False, restricted),
    }
    absent = frozenset((inactive.id, bot.id, foreign.id))
    sessions: dict[int, str] = {}

    def headers(mode: str, actor: UserProfile) -> dict[str, str]:
        if mode == "basic":
            token = b64encode(f"{actor.delivery_email}:{actor.api_key}".encode()).decode()
            return {"Authorization": f"Basic {token}"}
        if actor.id not in sessions:
            session = SessionStore()
            session.update(
                {
                    "_auth_user_id": str(actor.id),
                    "_auth_user_backend": "zproject.backends.DevAuthBackend",
                    "_auth_user_hash": actor.get_session_auth_hash(),
                }
            )
            session.create()
            cleanup.callback(session.delete)
            assert session.session_key is not None
            sessions[actor.id] = session.session_key
        return {"Cookie": f"{settings.SESSION_COOKIE_NAME}={sessions[actor.id]}"}

    def case(
        mode: str,
        label: str,
        actor: UserProfile,
        fields: dict[int, tuple[bool, int]] | None = None,
        *,
        excluded: frozenset[int] = absent,
        status: int = 200,
        method: str = "GET",
        message: str | None = None,
        language: str | None = None,
        query: str = "",
        anonymous: bool = False,
        host: str | None = None,
        sibling: bool = False,
    ) -> ConsentCase:
        prefix = "/api/v1" if mode == "basic" else "/json"
        headers_to_send = {} if anonymous else headers(mode, actor)
        if language:
            headers_to_send["Accept-Language"] = language
        mapped = actor.realm == member.realm and method != "OPTIONS" and not sibling
        return ConsentCase(
            f"{mode} {label}",
            method,
            host or actor.realm.host,
            headers_to_send,
            prefix + "/export/realm" + ("" if sibling else "/consents") + query,
            status,
            fields,
            excluded,
            message,
            mapped=mapped,
            also_compare_direct=not mapped and actor.realm != member.realm and method == "GET",
        )

    modes = ("basic", "session")
    reads = [case(mode, "ordinary administrator read", admin, flags) for mode in modes]
    cases = [*reads, *(replace(read, label=f"{read.label} HEAD", method="HEAD") for read in reads)]
    for mode in modes:
        cases.extend(
            (
                case(mode, "owner gets same scoped fields", owner, flags),
                case(
                    mode,
                    "member denied",
                    member,
                    status=400,
                    message="Must be an organization administrator",
                ),
                case(
                    mode,
                    "moderator denied",
                    moderator,
                    status=400,
                    message="Must be an organization administrator",
                ),
                case(
                    mode,
                    "guest denied",
                    guest,
                    status=400,
                    message="Must be an organization administrator",
                ),
                case(
                    mode,
                    "ordinary bot denied",
                    bot,
                    status=400,
                    message="Must be an organization administrator",
                ),
                case(mode, "member denied French", member, status=400, language="fr"),
                case(mode, "member denied HEAD", member, status=400, method="HEAD"),
                case(
                    mode,
                    "query extras do not change the result",
                    admin,
                    flags,
                    query="?client=website&other_parameter=1",
                ),
                case(
                    mode,
                    "member extra query remains denied",
                    member,
                    status=400,
                    message="Must be an organization administrator",
                    query="?client=website&other_parameter=1",
                ),
                case(
                    mode,
                    "other host administers only its own synthetic realm",
                    foreign,
                    {foreign.id: (True, restricted)},
                    excluded=frozenset((member.id, owner.id, admin.id, inactive.id, bot.id)),
                ),
                case(mode, "allowlisted port", admin, flags, host="zulip.testserver:8880"),
                case(mode, "without authorization", admin, status=401, anonymous=True),
                case(mode, "Django OPTIONS", admin, status=204, method="OPTIONS", anonymous=True),
                case(mode, "Django export list remains separate", admin, sibling=True),
            )
        )
    return cases


def validate(case: ConsentCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method == "HEAD":
        assert result[1] == b"", case.label
    elif case.flags is not None:
        data = json.loads(result[1])
        assert set(data) == {"result", "msg", "export_consents"}, case.label
        rows = data["export_consents"]
        assert all(
            set(row) == {"user_id", "consented", "email_address_visibility"} for row in rows
        ), case.label
        values = {
            row["user_id"]: (row["consented"], row["email_address_visibility"]) for row in rows
        }
        assert not case.absent & values.keys(), case.label
        assert {user_id: values[user_id] for user_id in case.flags} == case.flags, case.label
    elif case.status == 400:
        denied = json.loads(result[1])
        assert set(denied) == {"result", "msg", "code"}, case.label
        assert denied["code"] == "UNAUTHORIZED_PRINCIPAL", case.label
        if case.message is not None:
            assert denied["msg"] == case.message, case.label


def compare(case: ConsentCase, actual: ReadResult, expected: ReadResult) -> None:
    validate(case, actual)
    if case.method == "GET":
        got, want = json.loads(actual[1]), json.loads(expected[1])
        if case.flags is not None:
            got["export_consents"].sort(key=lambda row: row["user_id"])
            want["export_consents"].sort(key=lambda row: row["user_id"])
        assert got == want, case.label
    else:
        assert actual[1] == expected[1], case.label
    compare_read_headers(actual, expected, case.label)


def startup_guards(stage: Path, reads: list[ConsentCase]) -> None:
    db = connection.settings_dict
    assert not db["PASSWORD"]
    dsn = make_dsn(dbname=db["NAME"], host=db["HOST"], user=db["USER"], port=db["PORT"])
    for extra, error in (
        ({}, "Export consents read pilot requires ZULIP_RUST_DATABASE_URL"),
        (
            {"ZULIP_RUST_DATABASE_URL": dsn, "ZULIP_RUST_EXTERNAL_HOST": settings.EXTERNAL_HOST},
            "Export consents read pilot requires signed Django sessions",
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
        tempfile.TemporaryDirectory(prefix="export-consents-", dir=root) as tmp,
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
            map_variable="zulip_rust_export_consents",
            non_read_methods=("POST", "PATCH", "DELETE", "OPTIONS"),
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
