# ruff: noqa: INP001
"""Check normal read accounting on a disposable Redis through Django and Rust."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from base64 import b64encode
from contextlib import ExitStack
from pathlib import Path

import django
from django.conf import settings
from read_proxy_fixture import (
    ReadCase,
    private_read_proxy,
    validate_fixture,
    validate_local_root,
    verify_private_database,
)
from read_quota_fixture import LIMIT, QuotaCounter, private_redis, rust_quota_environment

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
    assert len(cases) * 3 < LIMIT // 2
    details: dict[str, bytes] = {}

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
        counter = QuotaCounter(db, quota, user.id)
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

        def check_phase(upstream: str, label: str) -> None:
            for case in cases:
                before = counter.sample()
                result, peer = proxy.observe(case.method, case.host, case.headers, case.path)
                status, body, _, _ = result
                assert (status, peer) == (case.status, upstream), (label, case.label, status, peer)
                counter.record_allowed(before, result, case.label)
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
                f"({LIMIT - counter.count}/{LIMIT} left)",
                flush=True,
            )

        wait_for_target(proxy.django_address)
        proxy.assert_untrusted_tls_rejected(user.realm.host, "/api/v1/saved_snippets")
        assert counter.sample().arrival is None
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
                **rust_quota_environment(quota),
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
        counter.verify_total(3 * len(cases))
        print(
            f"One Redis bucket continued across all {counter.count} measured requests.", flush=True
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
