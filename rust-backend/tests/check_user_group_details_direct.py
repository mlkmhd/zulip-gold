# ruff: noqa: INP001
"""Verify direct and optional proxy user-group detail reads with isolated Django."""

from __future__ import annotations

import argparse
import json
import os
import socket
import stat
import subprocess
import tempfile
from base64 import b64encode
from contextlib import ExitStack, closing
from dataclasses import dataclass, replace
from http.client import HTTPConnection
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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
    process,
    reserve_port,
    validate_fixture,
    validate_local_root,
    verify_private_database,
    wait_until,
)

from zerver.lib.test_rust_http import request_test_http, rust_http_service

if TYPE_CHECKING:
    from zerver.models import UserProfile

ROOT = Path(__file__).resolve().parents[2]
PILOT = "ZULIP_RUST_USER_GROUP_DETAIL_READ_PILOT"
LIST_PILOT = "ZULIP_RUST_USER_GROUP_READ_PILOT"
MAP = "user-group-details-map.conf"
ROUTES = "user-group-details-routes.conf"
STAGING = ROOT / "rust-backend/staging/nginx"


@dataclass(frozen=True)
class DetailCase(ReadCase):
    kind: Literal["members", "subgroups"]


def startup_guards() -> None:
    db = connection.settings_dict
    assert not db["PASSWORD"]
    dsn = make_dsn(dbname=db["NAME"], host=db["HOST"], user=db["USER"], port=db["PORT"])
    for extra, error in (
        ({}, "User-group detail read pilot requires ZULIP_RUST_DATABASE_URL"),
        (
            {"ZULIP_RUST_DATABASE_URL": dsn, "ZULIP_RUST_EXTERNAL_HOST": settings.EXTERNAL_HOST},
            "User-group detail read pilot requires signed Django sessions",
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


def make_cases(root: Path, cleanup: ExitStack) -> list[DetailCase]:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.models import UserProfile

    evidence = root / "user-group-details.json"
    metadata = evidence.lstat()
    assert stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid()
    references = json.loads(evidence.read_text())
    assert len(references) == 62
    sessions: dict[int, str] = {}
    user_ids = {row["user"] for row in references}
    users = UserProfile.objects.select_related("realm").in_bulk(user_ids)
    assert users.keys() == user_ids

    def basic(user: UserProfile) -> dict[str, str]:
        token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def browser(user: UserProfile) -> dict[str, str]:
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

    cases: list[DetailCase] = []
    initial: dict[tuple[str, str], DetailCase] = {}
    head_choices: set[tuple[str, str, str]] = set()
    for row in references:
        mode, kind = row["mode"], row["kind"]
        assert mode in {"basic", "session"} and kind in {"members", "subgroups"}
        user = users[row["user"]]
        assert user.realm_id == row["realm"]
        prefix, credentials = (
            ("/api/v1", basic(user)) if mode == "basic" else ("/json", browser(user))
        )
        path = f"{prefix}/user_groups/{row['group']}/{kind}"
        if row["query"] is not None:
            path += f"?{row['query']}"
        label = f"{mode} {kind} case {len(cases) + 1}"
        case = DetailCase(label, "GET", user.realm.host, credentials, path, row["status"], kind)
        cases.append(case)
        if row["status"] == 200 and row["query"] is None:
            initial.setdefault((mode, kind), case)

        # One HEAD for each key behavior, route and authentication mode.
        message = row["payload"].get("msg", "success")
        if row["query"] is None and (mode, kind, message) not in head_choices:
            head_choices.add((mode, kind, message))
            cases.append(
                DetailCase(f"{label} HEAD", "HEAD", case.host, credentials, path, case.status, kind)
            )
        if (
            message in {"Not allowed for guest users", "User group is deactivated."}
            and row["query"] is None
        ):
            cases.append(
                DetailCase(
                    f"{label} French",
                    "GET",
                    case.host,
                    {**credentials, "Accept-Language": "fr"},
                    path,
                    400,
                    kind,
                )
            )

    assert len(initial) == 4
    for (mode, kind), case in initial.items():
        prefix, id, _ = case.path.rsplit("/", 2)
        cases.extend(
            (
                DetailCase(
                    f"{mode} {kind} leading zero",
                    "GET",
                    case.host,
                    case.headers,
                    f"{prefix}/0{id}/{kind}",
                    200,
                    case.kind,
                ),
                DetailCase(
                    f"{mode} {kind} large missing ID",
                    "GET",
                    case.host,
                    case.headers,
                    f"{prefix}/{2**63}/{kind}",
                    400,
                    case.kind,
                ),
                DetailCase(
                    f"{mode} {kind} anonymous", "GET", case.host, {}, case.path, 401, case.kind
                ),
            )
        )
    return cases


def validate(case: DetailCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method == "HEAD":
        assert result[1] == b"", case.label


def compare(case: DetailCase, actual: ReadResult, expected: ReadResult) -> None:
    validate(case, actual)
    if case.method == "GET":
        candidate, target = json.loads(actual[1]), json.loads(expected[1])
        if case.status == 200 and case.kind in candidate and case.kind in target:
            candidate[case.kind].sort()
            target[case.kind].sort()
        assert candidate == target, case.label
    else:
        assert actual[1] == expected[1], case.label
    compare_read_headers(actual, expected, case.label)


def verify_method_map(
    nginx: Path,
    stage: Path,
    cases: tuple[ReadCase, ...],
    *,
    map_filename: str = MAP,
    map_variable: str = "zulip_rust_user_group_details",
) -> None:
    # Load the exact versioned map without an app, upstream, credentials, or body.
    # Every method terminates with 204 in this separate diagnostic server.
    stage.mkdir(mode=0o700)
    listen = reserve_port()
    conf = stage / "method-map.conf"
    log = stage / "nginx.log"
    command = [str(nginx), "-p", f"{stage}/", "-c", str(conf), "-e", str(log)]
    temp_dirs = "\n".join(
        f"    {name}_temp_path {stage}/{name}-temp;"
        for name in ("client_body", "proxy", "uwsgi", "fastcgi", "scgi")
    )
    conf.write_text(
        f"""
worker_processes 1;
pid {stage}/nginx.pid;
error_log {log};
events {{ worker_connections 32; }}
http {{
{temp_dirs}
    access_log off;
    include {STAGING / map_filename};
    server {{
        listen 127.0.0.1:{listen};
        location / {{
            add_header X-Test-Selection ${map_variable} always;
            return 204;
        }}
    }}
}}
"""
    )
    subprocess.run([*command, "-t"], check=True, timeout=8)

    def marker(case: DetailCase, method: str) -> str | None:
        with closing(HTTPConnection("127.0.0.1", listen, timeout=5)) as client:
            client.request(method, case.path, headers={"Host": case.host})
            reply = client.getresponse()
            assert reply.status == 204 and reply.read() == b""
            return reply.getheader("X-Test-Selection")

    with process([*command, "-g", "daemon off;"], stage / "start.log"):
        wait_until(lambda: marker(cases[0], "GET") == "1")
        for case in cases:
            assert marker(case, "GET") == marker(case, "HEAD") == "1", case.label
            for method in ("POST", "OPTIONS"):
                assert marker(case, method) == "0", (case.label, method)
    print(f"Exact method map verified on {len(cases)} paths without reaching either upstream.")


def proxy_exercise(
    args: argparse.Namespace,
    directory: Path,
    cases: list[DetailCase],
    default_cases: tuple[DetailCase, ...],
) -> None:
    from zerver.models import UserProfile

    # The first eight positive cases cover both operation kinds and both auth modes
    # on graceful reload and while Rust is stopped.
    routes = list(default_cases)
    selected = routes + [
        replace(case, label=f"{case.label} HEAD", method="HEAD") for case in routes
    ]
    for case in cases:
        if case.method != "GET" or case in routes:
            continue
        mapped = case.host == "zulip.testserver"
        if (
            not mapped
            or case.status == 401
            or case.label.endswith(("French", "leading zero", "large missing ID"))
            or ("?direct_" in case.path and case.status in {200, 400})
        ):
            selected.append(replace(case, mapped=mapped, also_compare_direct=not mapped))

    member_id = UserProfile.objects.get(
        realm__string_id="zulip", delivery_email="hamlet@zulip.com"
    ).id
    for case in routes:
        selected.extend(
            (
                replace(
                    case,
                    label=f"{case.label} OPTIONS",
                    method="OPTIONS",
                    status=204,
                    headers={"Access-Control-Request-Method": "POST"},
                    mapped=False,
                ),
                replace(case, label=f"{case.label} hostname port", host=f"{case.host}:8880"),
            )
        )
        if case.kind == "members":
            selected.append(
                replace(
                    case,
                    label=f"{case.label} excluded single-user membership",
                    path=f"{case.path}/{member_id}",
                    mapped=False,
                )
            )
    verify_method_map(args.nginx, directory / "methods", default_cases)
    with private_read_proxy(
        ROOT, directory, args.nginx, args.uwsgi, args.venv, (MAP,), openssl_bin=args.openssl
    ) as proxy:
        exercise_paired_read_routes(
            proxy,
            directory,
            ROUTES,
            selected,
            validate=validate,
            compare=compare,
            rust_extra_env={PILOT: "1"},
            paired_surfaces=4,
        )


def exercise(args: argparse.Namespace, root: Path, directory: Path, cleanup: ExitStack) -> None:
    cases = make_cases(root, cleanup)
    private_secret = directory / "synthetic-signing-input"
    private_secret.write_text(settings.SECRET_KEY)
    private_secret.chmod(0o600)
    session_env = {
        "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(private_secret),
        "ZULIP_RUST_DJANGO_AUTH_BACKENDS": json.dumps(settings.AUTHENTICATION_BACKENDS),
        "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
        "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
    }

    # Compare the four distinct API and web paths with no forwarding change.
    paths = {
        (case.path.split("/", 2)[1], case.kind): case
        for case in reversed(cases)
        if case.status == 200 and case.method == "GET" and "?" not in case.path
    }
    assert len(paths) == 4
    default_cases = tuple(paths.values())
    first = default_cases[0]
    if args.with_proxy:
        proxy_exercise(args, directory, cases, default_cases)
        return

    with private_read_proxy(ROOT, directory, args.nginx, args.uwsgi, args.venv, ()) as proxy:
        wait_until(lambda: proxy.request("GET", first.host, first.headers, first.path)[0] == 200)
        with rust_http_service({**session_env, LIST_PILOT: "1"}) as off_port:
            for case in default_cases:
                for method in ("GET", "HEAD"):
                    actual = request_test_http(off_port, method, case.host, case.headers, case.path)
                    assert actual[0] == 404, (
                        method,
                        case.kind,
                        "detail pilot enabled by list flag",
                    )

        with rust_http_service({**session_env, PILOT: "1"}) as port:
            for case in default_cases:
                prefix, _, _ = case.path.rsplit("/", 2)
                actual = request_test_http(port, "GET", case.host, case.headers, prefix)
                assert actual[0] == 404, (case.kind, "list pilot enabled by detail flag")
            for case in cases:
                expected, upstream = proxy.observe(case.method, case.host, case.headers, case.path)
                assert upstream == proxy.django_address, case.label
                actual = request_test_http(
                    port, case.method, case.host, case.headers, case.path, include_date=True
                )
                compare(case, actual, expected)

            for case in default_cases:
                expected, upstream = proxy.observe("OPTIONS", case.host, {}, case.path)
                assert expected[0] == 204 and upstream == proxy.django_address
                with closing(HTTPConnection("127.0.0.1", port, timeout=5)) as client:
                    client.request("OPTIONS", case.path, headers={"Host": case.host})
                    response = client.getresponse()
                    allow = response.getheader("Allow")
                    assert response.status == 405 and allow is not None
                    assert {method.strip() for method in allow.split(",")} == {"GET", "HEAD"}
                    response.read()
                prefix, _, kind = case.path.rsplit("/", 2)
                invalid = f"{prefix}/not-a-group/{kind}"
                assert request_test_http(port, "GET", case.host, case.headers, invalid)[0] == 404

    print(
        f"{len(cases)} group-detail HTTP comparisons, including "
        f"{sum(case.method == 'HEAD' for case in cases)} empty HEAD responses; "
        "numeric, method and independent opt-in guards passed."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--uwsgi", type=Path, required=True)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument(
        "--with-proxy", action="store_true", help="also rehearse private TLS routing and rollback"
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
    startup_guards()
    with (
        tempfile.TemporaryDirectory(prefix="detail-http-", dir=root) as tmp,
        ExitStack() as cleanup,
    ):
        exercise(args, root, Path(tmp), cleanup)


if __name__ == "__main__":
    main()
