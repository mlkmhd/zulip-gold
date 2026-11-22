# ruff: noqa: INP001
"""Rehearse read-only Basic and session channel list in a private TLS proxy."""

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
from urllib.parse import urlencode

import django
from check_channel_details import make_cases as seed_details
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
    wait_until,
)

from zerver.lib.test_rust_http import request_test_http, rust_http_service

if TYPE_CHECKING:
    from zerver.models import UserProfile

ROOT = Path(__file__).resolve().parents[2]
PILOT = "ZULIP_RUST_CHANNEL_LIST_READ_PILOT"
MAP = "channel-list-map.conf"
ROUTES = "channel-list-routes.conf"


@dataclass(frozen=True)
class ListCase(ReadCase):
    included: frozenset[int]
    excluded: frozenset[int]
    message: str | None


def make_cases(cleanup: ExitStack) -> list[ListCase]:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.lib.create_user import create_user
    from zerver.lib.streams import create_stream_if_needed
    from zerver.models import DefaultStream, Stream, UserProfile

    seed_details(cleanup)
    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = UserProfile.objects.get(realm=member.realm, delivery_email="polonius@zulip.com")
    admin = UserProfile.objects.get(realm=member.realm, delivery_email="iago@zulip.com")
    target = UserProfile.objects.get(realm=member.realm, delivery_email="prospero@zulip.com")
    bot = UserProfile.objects.get(realm=member.realm, delivery_email="default-bot@zulip.com")
    foreign = UserProfile.objects.filter(
        realm__string_id="zephyr", role=UserProfile.ROLE_MEMBER, is_active=True, is_bot=False
    ).first()
    assert foreign is not None and guest.is_guest and bot.is_bot and admin.is_realm_admin
    UserProfile.objects.filter(id=bot.id).update(bot_owner_id=target.id)
    legacy = create_user(
        email="legacy-channel-pilot@example.invalid",
        password=None,
        realm=member.realm,
        full_name="Legacy channel pilot bot",
        bot_type=UserProfile.DEFAULT_BOT,
    )
    assert legacy.is_active and legacy.is_bot and legacy.bot_owner_id is None

    def stream(name: str) -> Stream:
        return Stream.objects.get(realm=member.realm, name=name)

    public = stream("Pilot subscription status").id
    private = stream("Pilot private subscriptions").id
    web = stream("Pilot web subscriptions").id
    archived = stream("Pilot archived subscriptions").id
    group_admin = stream("Pilot metadata can_administer_channel_group").id
    group_add = stream("Pilot metadata can_add_subscribers_group").id
    group_sub = stream("Pilot metadata can_subscribe_group").id
    foreign_channel = Stream.objects.get(realm=foreign.realm, name="Pilot foreign subscription").id
    DefaultStream.objects.get_or_create(realm=member.realm, stream_id=public)
    for name in ("Pilot list É", "Pilot list E\u0301", "Pilot list فارسی"):
        create_stream_if_needed(member.realm, name)

    sessions: dict[int, str] = {}

    def auth_headers(mode: str, actor: UserProfile) -> dict[str, str]:
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
        expected: tuple[int, ...] = (),
        absent: tuple[int, ...] = (),
        *,
        query: str = "",
        message: str | None = None,
        status: int = 200,
        method: str = "GET",
        language: str | None = None,
        anonymous: bool = False,
        host: str | None = None,
        suffix: str = "",
    ) -> ListCase:
        path = ("/api/v1" if mode == "basic" else "/json") + "/streams" + suffix
        if query:
            path += "?" + query
        headers = {} if anonymous else auth_headers(mode, actor)
        if language:
            headers["Accept-Language"] = language
        mapped = actor.realm == member.realm and method != "OPTIONS" and not suffix
        return ListCase(
            f"{mode} {label}",
            method,
            host or actor.realm.host,
            headers,
            path,
            status,
            frozenset(expected),
            frozenset(absent),
            message,
            mapped=mapped,
            also_compare_direct=not mapped and actor.realm != member.realm and method == "GET",
        )

    def options(**kwargs: bool) -> str:
        return urlencode({n: json.dumps(v) for n, v in kwargs.items()})

    none = options(include_public=False, include_subscribed=False)
    modes = ("basic", "session")
    reads = [
        case(mode, "default channels", member, (public, web), (private, archived, foreign_channel))
        for mode in modes
    ]
    cases = [*reads, *(replace(read, label=f"{read.label} HEAD", method="HEAD") for read in reads)]
    for mode in modes:
        cases.extend(
            (
                case(
                    mode,
                    "guest default excludes unrelated channels",
                    guest,
                    (private,),
                    (public, web),
                ),
                case(
                    mode,
                    "guest scoped web public only",
                    guest,
                    (web,),
                    (public, private),
                    query=none + "&include_web_public=true",
                ),
                case(
                    mode,
                    "guest no sources returns empty",
                    guest,
                    absent=(private, web, public),
                    query=none,
                ),
                case(
                    mode,
                    "metadata-only grants",
                    member,
                    (public, group_admin, group_add, group_sub),
                    (private, archived),
                    query=none + "&include_all=true",
                ),
                case(
                    mode,
                    "content skips admin-only group",
                    member,
                    (public, web, group_add, group_sub),
                    (private, group_admin),
                    query=none + "&include_can_access_content=true",
                ),
                case(
                    mode,
                    "administrator metadata",
                    admin,
                    (private, group_admin),
                    (archived,),
                    query="include_all=true",
                ),
                case(
                    mode,
                    "administrator content not all metadata",
                    admin,
                    (public, web),
                    (private,),
                    query="include_can_access_content=true",
                ),
                case(
                    mode,
                    "legacy all flag respects archive option",
                    member,
                    (public, archived, group_admin),
                    (private,),
                    query=options(include_all_active=True, exclude_archived=False),
                ),
                case(
                    mode,
                    "archive option with only active web public",
                    guest,
                    (web,),
                    (archived,),
                    query=none + "&include_web_public=true&exclude_archived=false",
                ),
                case(
                    mode,
                    "metadata wins over narrower content option",
                    member,
                    (group_admin, group_sub),
                    (private,),
                    query=options(include_all=True, include_can_access_content=True),
                ),
                case(
                    mode,
                    "include default boolean and Unicode data order",
                    member,
                    (public,),
                    (private,),
                    query="include_default=true",
                ),
                case(
                    mode,
                    "include own subscriptions",
                    guest,
                    (private,),
                    (web, public),
                    query=options(include_public=False, include_subscribed=True),
                ),
                case(
                    mode,
                    "owners option does not expand human access",
                    member,
                    absent=(private, web, public),
                    query=none + "&include_owner_subscribed=true",
                ),
                case(
                    mode,
                    "ignored options and Django query behavior",
                    member,
                    (public,),
                    (),
                    query="include_public=false&include_public=true&other_option=1&client=website",
                ),
                case(
                    mode,
                    "strict Boolean",
                    member,
                    status=400,
                    message="include_public is not a boolean",
                    query="include_public=1",
                ),
                case(
                    mode,
                    "strict JSON and error precedence",
                    member,
                    status=400,
                    message="include_all is not valid JSON",
                    query="include_all=True&include_public=1",
                ),
                case(
                    mode,
                    "localized invalid option",
                    member,
                    status=400,
                    language="fr",
                    query="include_all=1",
                ),
                case(
                    mode,
                    "allowlisted staging port",
                    member,
                    (public,),
                    (),
                    host="zulip.testserver:8880",
                ),
                case(mode, "foreign Django host", foreign, (foreign_channel,), (private, public)),
                case(mode, "no authorization", member, status=401, anonymous=True),
                case(
                    mode,
                    "legacy bot with owner option fails closed",
                    legacy,
                    status=500,
                    message="Internal server error",
                    query="include_owner_subscribed=true",
                ),
                case(
                    mode,
                    "legacy bot failed HEAD has no content",
                    legacy,
                    status=500,
                    method="HEAD",
                    query="include_owner_subscribed=true",
                ),
                case(
                    mode,
                    "legacy bot French generic error",
                    legacy,
                    status=500,
                    language="fr",
                    query="include_owner_subscribed=true",
                ),
                case(
                    mode,
                    "legacy bot ordinary read without owner option",
                    legacy,
                    (public, web),
                    (private, archived),
                ),
                case(
                    mode,
                    "metadata overrides missing owner",
                    legacy,
                    (public, web),
                    (private, archived),
                    query="include_all=true&include_owner_subscribed=true",
                ),
                case(
                    mode,
                    "legacy all metadata overrides missing owner",
                    legacy,
                    (public, web),
                    (private, archived),
                    query="include_all_active=true&include_owner_subscribed=true",
                ),
                case(
                    mode,
                    "legacy owner must exist even with no other sources",
                    legacy,
                    status=500,
                    message="Internal server error",
                    query=none + "&include_owner_subscribed=true",
                ),
                case(
                    mode,
                    "parameter validation precedes missing legacy owner",
                    legacy,
                    status=400,
                    message="include_public is not a boolean",
                    query="include_public=1&include_owner_subscribed=true",
                ),
                case(mode, "Django OPTIONS", member, status=204, method="OPTIONS", anonymous=True),
                case(mode, "other channel detail remains Django", member, suffix=f"/{public}"),
            )
        )
    cases.extend(
        (
            case(
                "basic",
                "bot only its recorded owner's channels",
                bot,
                (private, web),
                (public,),
                query=none + "&include_owner_subscribed=true",
            ),
            case(
                "basic",
                "bot own and owner channels",
                bot,
                (public, private, web),
                (archived,),
                query=options(
                    include_public=False, include_subscribed=True, include_owner_subscribed=True
                ),
            ),
            case(
                "basic",
                "bot owner and content",
                bot,
                (public, private, web, group_admin),
                query=none + "&include_owner_subscribed=true&include_can_access_content=true",
            ),
            case(
                "basic",
                "bot metadata suppresses owner option",
                bot,
                (public, web),
                (private,),
                query=none + "&include_all=true&include_owner_subscribed=true",
            ),
        )
    )
    return cases


def validate(case: ListCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method == "HEAD":
        assert result[1] == b"", case.label
    elif case.status == 500:
        error = json.loads(result[1])
        assert set(error) == {"result", "msg"} and error["result"] == "error", case.label
        if case.message is not None:
            assert error["msg"] == case.message, case.label
        assert result[2]["Cache-Control"] is None, case.label
        expected_vary = "Accept-Language, Cookie" if "Cookie" in case.headers else "Accept-Language"
        assert result[2]["Vary"] == expected_vary, (case.label, result[2]["Vary"])
    elif case.method == "GET" and case.status == 200 and (case.included or case.excluded):
        data = json.loads(result[1])["streams"]
        ids = {item["stream_id"] for item in data}
        names = [item["name"] for item in data]
        assert names == sorted(names), case.label
        assert case.included <= ids and not case.excluded & ids, (
            case.label,
            case.included - ids,
            case.excluded & ids,
        )
        assert all("email_token" not in item for item in data), case.label
        if "include_default=true" in case.path:
            assert all(isinstance(item["is_default"], bool) for item in data), case.label
            assert all(item["is_default"] for item in data if item["stream_id"] in case.included), (
                case.label
            )
        else:
            assert all("is_default" not in item for item in data), case.label
    elif case.message is not None:
        assert json.loads(result[1])["msg"] == case.message, case.label


def compare(case: ListCase, actual: ReadResult, expected: ReadResult) -> None:
    validate(case, actual)
    if case.method == "GET":
        got, want = json.loads(actual[1]), json.loads(expected[1])
        assert got == want, (case.label, got, want)
    else:
        assert actual[1] == expected[1], case.label
    compare_read_headers(actual, expected, case.label)


def startup_guards(stage: Path, reads: list[ListCase]) -> None:
    db = connection.settings_dict
    assert not db["PASSWORD"]
    dsn = make_dsn(dbname=db["NAME"], host=db["HOST"], user=db["USER"], port=db["PORT"])
    for extra, error in (
        ({}, "Channel list read pilot requires ZULIP_RUST_DATABASE_URL"),
        (
            {"ZULIP_RUST_DATABASE_URL": dsn, "ZULIP_RUST_EXTERNAL_HOST": settings.EXTERNAL_HOST},
            "Channel list read pilot requires signed Django sessions",
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
        tempfile.TemporaryDirectory(prefix="channel-list-", dir=root) as tmp,
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
            map_variable="zulip_rust_channel_list",
            non_read_methods=("POST", "PATCH", "DELETE", "OPTIONS"),
        )
        with private_read_proxy(
            ROOT,
            stage,
            args.nginx,
            args.uwsgi,
            args.venv,
            (MAP,),
            openssl_bin=args.openssl,
            django_json_server_errors=True,
        ) as proxy:
            # Resolve Django's first route with a French request. Its typed
            # validation string is still English; generic 500s use French.
            cold = next(case for case in cases if case.label == "basic localized invalid option")

            def cold_french_request() -> bool:
                result = proxy.request(cold.method, cold.host, cold.headers, cold.path)
                if result[0] != 400:
                    return False
                assert json.loads(result[1])["msg"] == "include_all is not a boolean"
                assert result[2]["Content-Language"] == "fr"
                return True

            wait_until(cold_french_request)
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
