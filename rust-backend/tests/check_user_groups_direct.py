# ruff: noqa: INP001
"""Verify direct and opt-in proxy user-group reads in a private fixture."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
from base64 import b64encode
from contextlib import ExitStack, closing
from http.client import HTTPConnection
from pathlib import Path

import django
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

ROOT = Path(__file__).resolve().parents[2]
API = "/api/v1/user_groups"
WEB = "/json/user_groups"
PILOT = "ZULIP_RUST_USER_GROUP_READ_PILOT"
MAP = "user-groups-map.conf"
ROUTE = "user-groups-routes.conf"


def verify_startup_guards() -> None:
    db = connection.settings_dict
    assert not db["PASSWORD"]
    synthetic_database = make_dsn(
        dbname=db["NAME"],
        host=db["HOST"],
        user=db["USER"],
        port=db["PORT"],
    )
    for extra, expected_error in (
        ({}, "User-group read pilot requires ZULIP_RUST_DATABASE_URL"),
        (
            {
                "ZULIP_RUST_DATABASE_URL": synthetic_database,
                "ZULIP_RUST_EXTERNAL_HOST": settings.EXTERNAL_HOST,
            },
            "User-group read pilot requires signed Django sessions",
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
        assert result.returncode != 0 and expected_error in result.stderr


def validate(case: ReadCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method == "HEAD":
        assert result[1] == b"", case.label
    if case.headers.get("Accept-Language") == "fr":
        assert result[2]["Content-Language"] == "fr", case.label
        assert json.loads(result[1])["msg"] != "Not allowed for guest users", case.label


def compare(case: ReadCase, actual: ReadResult, expected: ReadResult) -> None:
    validate(case, actual)
    if case.method == "GET":
        assert json.loads(actual[1]) == json.loads(expected[1]), case.label
    else:
        assert actual[1] == expected[1], case.label
    compare_read_headers(actual, expected, case.label)


def compare_transport(args: argparse.Namespace, directory: Path, cleanup: ExitStack) -> None:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.models import NamedUserGroup, UserProfile

    users = UserProfile.objects.filter(is_active=True, is_bot=False)
    member = users.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = users.get(
        realm=member.realm, role=UserProfile.ROLE_GUEST, delivery_email="polonius@zulip.com"
    )
    admin = users.filter(realm=member.realm, role=UserProfile.ROLE_REALM_ADMINISTRATOR).first()
    other = users.filter(realm__string_id="zephyr", role=UserProfile.ROLE_MEMBER).first()
    assert admin is not None and other is not None

    def basic(user: UserProfile) -> dict[str, str]:
        encoded = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    def session(user: UserProfile) -> dict[str, str]:
        store = SessionStore()
        store.update(
            {
                "_auth_user_id": str(user.id),
                "_auth_user_backend": "zproject.backends.DevAuthBackend",
                "_auth_user_hash": user.get_session_auth_hash(),
            }
        )
        store.create()
        cleanup.callback(store.delete)
        assert store.session_key is not None
        return {"Cookie": f"{settings.SESSION_COOKIE_NAME}={store.session_key}"}

    cases = []
    excluded = []
    field = "include_deactivated_groups"
    for mode, path, auth in (("Basic", API, basic), ("Session", WEB, session)):
        host = member.realm.host
        credentials, guest_credentials = auth(member), auth(guest)
        for name, method, target_host, headers, query, status in (
            ("member", "GET", host, credentials, "", 200),
            ("member HEAD", "HEAD", host, credentials, "", 200),
            ("include deactivated", "GET", host, credentials, f"?{field}=true", 200),
            (
                "duplicate and ignored parameters",
                "GET",
                host,
                credentials,
                f"?z=1&{field}=false&client=ZulipMobile&{field}=true&a=2",
                200,
            ),
            ("invalid JSON", "GET", host, credentials, f"?{field}=undefined", 400),
            ("invalid type", "GET", host, credentials, f"?{field}=1", 400),
            (
                "guest with invalid parameter",
                "GET",
                host,
                guest_credentials,
                f"?{field}=undefined",
                400,
            ),
            ("guest HEAD", "HEAD", host, guest_credentials, "", 400),
            (
                "guest localized",
                "GET",
                host,
                {**guest_credentials, "Accept-Language": "fr"},
                "",
                400,
            ),
            ("realm administrator", "GET", host, auth(admin), "", 200),
            ("other realm member", "GET", other.realm.host, auth(other), "", 200),
            ("anonymous JSON", "GET", host, {}, "", 401),
        ):
            cases.append(
                ReadCase(
                    f"{mode} {name}",
                    method,
                    target_host,
                    headers,
                    path + query,
                    status,
                    mapped=target_host == host,
                    also_compare_direct=target_host != host,
                )
            )
        excluded.append(ReadCase(f"{mode} OPTIONS", "OPTIONS", host, {}, path, 204, mapped=False))

    if args.with_proxy:
        group = NamedUserGroup.objects.get(realm=member.realm, name="Pilot group")
        cases.extend(excluded)
        for mode, path, auth in (("Basic", API, basic), ("Session", WEB, session)):
            cases.extend(
                [
                    ReadCase(
                        f"{mode} excluded group detail",
                        "GET",
                        member.realm.host,
                        auth(member),
                        f"{path}/{group.id}/members",
                        200,
                        mapped=False,
                    ),
                    ReadCase(
                        f"{mode} allowed host with port",
                        "GET",
                        f"{member.realm.host}:8880",
                        auth(member),
                        path,
                        200,
                    ),
                ]
            )
        priority = {
            "Basic member": 0,
            "Session member": 1,
            "Basic member HEAD": 2,
            "Session member HEAD": 3,
        }
        cases.sort(key=lambda case: priority.get(case.label, 4))
        with private_read_proxy(
            ROOT, directory, args.nginx, args.uwsgi, args.venv, (MAP,), openssl_bin=args.openssl
        ) as proxy:
            exercise_paired_read_routes(
                proxy,
                directory,
                ROUTE,
                cases,
                validate=validate,
                compare=compare,
                rust_extra_env={PILOT: "1"},
            )
        return

    private_secret = directory / "synthetic-signing-input"
    private_secret.write_text(settings.SECRET_KEY)
    private_secret.chmod(0o600)
    session_env = {
        "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(private_secret),
        "ZULIP_RUST_DJANGO_AUTH_BACKENDS": json.dumps(settings.AUTHENTICATION_BACKENDS),
        "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
        "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
    }
    with private_read_proxy(ROOT, directory, args.nginx, args.uwsgi, args.venv, ()) as proxy:
        wait_until(lambda: proxy.request("GET", cases[0].host, cases[0].headers, API)[0] == 200)
        with rust_http_service(session_env) as disabled:
            for case in cases:
                if case.label not in {
                    "Basic member",
                    "Basic member HEAD",
                    "Session member",
                    "Session member HEAD",
                }:
                    continue
                status, _, _, _ = request_test_http(
                    disabled, case.method, case.host, case.headers, case.path
                )
                assert status == 404, (case.label, "pilot was not off by default")

        with rust_http_service({**session_env, PILOT: "1"}) as port:
            for case in cases:
                expected, upstream = proxy.observe(case.method, case.host, case.headers, case.path)
                assert upstream == proxy.django_address, case.label
                actual = request_test_http(
                    port, case.method, case.host, case.headers, case.path, include_date=True
                )
                compare(case, actual, expected)
            for case in excluded:
                response, upstream = proxy.observe(case.method, case.host, case.headers, case.path)
                assert response[0] == case.status and upstream == proxy.django_address
                with closing(HTTPConnection("127.0.0.1", port, timeout=5)) as client:
                    client.request("OPTIONS", case.path, headers={"Host": case.host})
                    excluded_response = client.getresponse()
                    allowed = excluded_response.getheader("Allow")
                    assert excluded_response.status == 405 and allowed is not None
                    assert {method.strip() for method in allowed.split(",")} == {"GET", "HEAD"}
                    excluded_response.read()

    print(
        f"{len(cases)} direct Rust versus Django HTTP comparisons; "
        "both paths stayed on Django in the proxy, default-off and startup guards passed."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--uwsgi", type=Path, required=True)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument(
        "--with-proxy", action="store_true", help="also rehearse opt-in and rollback"
    )
    parser.add_argument("--openssl", type=Path, help="OpenSSL for the private TLS certificate")
    args = parser.parse_args()
    if args.with_proxy != (args.openssl is not None):
        parser.error("--with-proxy and --openssl must be supplied together")
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("the direct pilot requires a private loopback-only network namespace")
    if any(name.startswith("ZULIP_RUST_") and name != "ZULIP_RUST_HTTP_BIN" for name in os.environ):
        raise RuntimeError("use a scrubbed environment with only ZULIP_RUST_HTTP_BIN configured")
    validate_fixture(root)
    django.setup()
    verify_private_database()
    verify_startup_guards()
    with (
        tempfile.TemporaryDirectory(prefix="groups-http-", dir=root) as tmp,
        ExitStack() as cleanup,
    ):
        compare_transport(args, Path(tmp), cleanup)


if __name__ == "__main__":
    main()
