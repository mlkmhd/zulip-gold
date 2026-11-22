# ruff: noqa: INP001
"""Check normal read accounting on a disposable Redis through Django and Rust."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from base64 import b64encode
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import django
import redis
from django.conf import settings
from read_proxy_fixture import (
    PrivateQuota,
    ReadCase,
    private_read_proxy,
    process,
    reserve_port,
    validate_fixture,
    validate_local_root,
    verify_private_database,
    wait_until,
)

from zerver.lib.test_rust_http import rust_http_service

ROOT = Path(__file__).resolve().parents[2]
MAPS = (
    "saved-snippets-map.conf",
    "saved-snippets-web-map.conf",
    "scheduled-messages-map.conf",
    "realm-domains-map.conf",
)
ROUTES = (
    ("saved-snippets-routes.conf", "saved-snippets-web-routes.conf"),
    ("scheduled-messages-routes.conf", "realm-domains-routes.conf"),
)
PATHS = ("/saved_snippets", "/scheduled_messages", "/realm/domains")
WINDOW = 14_400
LIMIT = 100
STEP = WINDOW // LIMIT


@contextmanager
def private_redis(directory: Path, redis_bin: Path) -> Iterator[tuple[redis.Redis, PrivateQuota]]:
    if not redis_bin.is_absolute():
        raise RuntimeError("supply an absolute redis-server path")
    socket_path = directory / "quota.sock"
    config = PrivateQuota(reserve_port(), WINDOW, LIMIT)
    arguments = [
        str(redis_bin),
        "--bind",
        "127.0.0.1",
        "--port",
        str(config.port),
        "--unixsocket",
        str(socket_path),
        "--unixsocketperm",
        "700",
        "--protected-mode",
        "yes",
        "--daemonize",
        "no",
        "--save",
        "",
        "--appendonly",
        "no",
        "--dir",
        str(directory),
        "--loglevel",
        "warning",
    ]
    with process(arguments, directory / "redis.log") as child:
        wait_until(lambda: child.poll() is None and socket_path.is_socket())
        client: redis.Redis = redis.Redis(unix_socket_path=str(socket_path), socket_timeout=2)
        try:
            server = client.info("server")
            assert server["process_id"] == child.pid and server["tcp_port"] == config.port
            assert client.config_get("bind") == {"bind": "127.0.0.1"}
            assert client.config_get("dir") == {"dir": str(directory)}
            assert client.dbsize() == 0, "Redis fixture must be empty"
            yield client, config
        finally:
            client.close()


def pilot(args: argparse.Namespace, directory: Path, cleanup: ExitStack) -> None:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.models import UserProfile

    user = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    readiness_user = UserProfile.objects.get(
        realm__string_id="zulip", delivery_email="othello@zulip.com"
    )
    assert user.realm.host == "zulip.testserver"
    assert user.rate_limits == readiness_user.rate_limits == ""

    def basic(profile: UserProfile) -> dict[str, str]:
        token = b64encode(f"{profile.delivery_email}:{profile.api_key}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

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
    browser = {"Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}"}
    cases = [
        ReadCase(
            f"{method} {prefix}{path}", method, user.realm.host, credentials, prefix + path, 200
        )
        for path in PATHS
        for prefix, credentials in (("/api/v1", basic(user)), ("/json", browser))
        for method in ("GET", "HEAD")
    ]
    assert WINDOW % LIMIT == 0 and len(cases) * 3 < LIMIT // 2
    key = f"ratelimit:RateLimitedUser:{user.id}:api_by_user:gcra"
    field = f"{WINDOW}:{LIMIT}"
    details: dict[str, bytes] = {}
    request_count = 0

    with (
        private_redis(directory, args.redis_server) as (db, quota),
        private_read_proxy(
            ROOT,
            directory,
            args.nginx,
            args.uwsgi,
            args.venv,
            MAPS,
            openssl_bin=args.openssl,
            quota=quota,
        ) as proxy,
    ):
        # Use a different legitimate synthetic member for readiness during
        # graceful reloads; no probe is charged to the measured account.
        ready_headers = basic(readiness_user)

        def wait_for_target(target: str) -> None:
            consecutive = 0
            latest: list[tuple[int, str]] = []
            for _ in range(6):
                try:
                    checks = [
                        proxy.observe("HEAD", user.realm.host, ready_headers, "/api/v1" + path)
                        for path in PATHS
                    ]
                except (ConnectionError, FileNotFoundError, TimeoutError):
                    consecutive = 0
                else:
                    latest = [(result[0], peer) for result, peer in checks]
                    if all(status == 200 and peer == target for status, peer in latest):
                        consecutive += 1
                        if consecutive >= 2:
                            return
                    else:
                        consecutive = 0
                time.sleep(0.35)
            raise RuntimeError(f"private route not ready within the probe budget: {latest}")

        def observed_tat() -> float | None:
            value = db.hget(key, field)
            return None if value is None else float(value)

        def check_phase(upstream: str, label: str) -> None:
            nonlocal request_count
            for case in cases:
                before = observed_tat()
                assert (before is None) == (request_count == 0)
                seconds, microseconds = db.time()
                start = seconds + microseconds / 1_000_000
                result, peer = proxy.observe(case.method, case.host, case.headers, case.path)
                after = observed_tat()
                seconds, microseconds = db.time()
                finish = seconds + microseconds / 1_000_000
                request_count += 1
                status, body, headers, _ = result
                assert (status, peer) == (case.status, upstream), (label, case.label, status, peer)
                assert after is not None and (before is None or before > finish)
                if before is None:
                    # Django's test clock and Redis are on this same host.
                    assert start + STEP - 0.05 <= after <= finish + STEP + 0.05
                else:
                    assert abs(after - before - STEP) < 1e-6, (label, case.label, after - before)
                assert headers["X-RateLimit-Limit"] == str(LIMIT), (label, case.label, "limit")
                assert headers["X-RateLimit-Remaining"] == str(LIMIT - request_count), (
                    label,
                    case.label,
                    "remaining",
                    headers["X-RateLimit-Remaining"],
                    request_count,
                )
                reset = headers["X-RateLimit-Reset"]
                assert reset is not None and abs(int(reset) - int(after)) <= 1
                assert headers["Retry-After"] is None
                if case.method == "HEAD":
                    assert body == b"", (label, case.label, "HEAD body")
                elif label == "Django default":
                    details[case.path] = body
                else:
                    actual, expected = json.loads(body), json.loads(details[case.path])
                    if case.path.endswith("/realm/domains"):
                        actual["domains"].sort(key=lambda row: row["domain"])
                        expected["domains"].sort(key=lambda row: row["domain"])
                    assert actual == expected, (label, case.label, "response changed")
            print(
                f"{label}: {len(cases)} allowed reads, exactly one Redis charge each "
                f"({LIMIT - request_count}/{LIMIT} left)",
                flush=True,
            )

        wait_for_target(proxy.django_address)
        proxy.assert_untrusted_tls_rejected(user.realm.host, "/api/v1/saved_snippets")
        assert observed_tat() is None
        check_phase(proxy.django_address, "Django default")
        secret = directory / "synthetic-secret"
        secret.write_text(settings.SECRET_KEY)
        secret.chmod(0o600)
        with rust_http_service(
            {
                "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                "ZULIP_RUST_DJANGO_AUTH_BACKENDS": json.dumps(settings.AUTHENTICATION_BACKENDS),
                "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
                "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
                "ZULIP_RUST_CLIENT_IP_SOURCE": "proxy",
                "ZULIP_RUST_REDIS_URL": f"redis://127.0.0.1:{quota.port}/0",
                "ZULIP_RUST_RATE_LIMIT_RULES": json.dumps([[WINDOW, LIMIT]]),
            }
        ) as rust_port:
            rust_address = f"127.0.0.1:{rust_port}"
            for index, routes in enumerate(ROUTES):
                proxy.enable_routes(routes, rust_port, upstreams_per_route=index + 1)
            wait_for_target(rust_address)
            check_phase(rust_address, "Rust opt-in")

        for routes in ROUTES:
            for route in routes:
                proxy.disable_route(route)
        wait_for_target(proxy.django_address)
        check_phase(proxy.django_address, "Django restored")
        assert request_count == 3 * len(cases)
        assert set(db.hkeys(key)) == {field.encode()}
        print(
            f"One Redis bucket continued across all {request_count} measured requests.", flush=True
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument(
        "--openssl", type=Path, help="enable private loopback TLS with this OpenSSL"
    )
    parser.add_argument("--redis-server", type=Path, required=True)
    parser.add_argument("--uwsgi", type=Path, required=True)
    parser.add_argument("--venv", type=Path, required=True)
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if any(name.startswith("ZULIP_RUST_") and name != "ZULIP_RUST_HTTP_BIN" for name in os.environ):
        raise RuntimeError("use a scrubbed environment with only ZULIP_RUST_HTTP_BIN configured")
    validate_fixture(root)
    django.setup()
    verify_private_database()
    with tempfile.TemporaryDirectory(prefix="net.", dir=root) as directory, ExitStack() as cleanup:
        pilot(args, Path(directory), cleanup)


if __name__ == "__main__":
    main()
