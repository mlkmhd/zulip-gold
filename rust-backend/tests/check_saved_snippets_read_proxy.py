# ruff: noqa: INP001
"""Run the staging read maps, private upstream outage and explicit local rollback."""

import argparse
import json
import os
import tempfile
import time
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from statistics import median

import django
from django.conf import settings
from read_proxy_fixture import (
    ReadResult,
    compare_read_headers,
    private_read_proxy,
    validate_fixture,
    validate_local_root,
    verify_private_database,
    wait_until,
)

from zerver.lib.test_rust_http import request_test_http, rust_http_service

ROOT = Path(__file__).resolve().parents[2]
API_PATH = "/api/v1/saved_snippets"
WEB_PATH = "/json/saved_snippets"
ROUTES = ("saved-snippets-routes.conf", "saved-snippets-web-routes.conf")


@dataclass(frozen=True)
class Case:
    label: str
    method: str
    host: str
    headers: dict[str, str]
    path: str
    status: int
    snippets: list[dict[str, object]] | None = None
    mapped: bool = True


def compare(actual: ReadResult, expected: ReadResult, label: str) -> None:
    assert actual[0] == expected[0], (label, "status", actual[0], expected[0])
    assert actual[1] == expected[1], (label, "response bytes")
    compare_read_headers(actual, expected, label)


def benchmark(port: int, case: Case, count: int) -> tuple[float, float]:
    def one(_: int) -> float:
        start = time.monotonic()
        status, body, _, _ = request_test_http(
            port, case.method, case.host, case.headers, case.path
        )
        elapsed = time.monotonic() - start
        assert status == 200 and json.loads(body)["saved_snippets"] == case.snippets, case.label
        return elapsed

    rounds, latencies = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, range(4)))
        for _ in range(3):
            start = time.monotonic()
            latencies.extend(pool.map(one, range(count)))
            rounds.append(count / (time.monotonic() - start))
    latencies.sort()
    return median(rounds), latencies[ceil(0.95 * len(latencies)) - 1] * 1000


def pilot(args: argparse.Namespace, directory: Path) -> None:
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.sessions.models import Session

    from zerver.models import SavedSnippet, UserProfile

    owner = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    other = UserProfile.objects.get(realm__string_id="zulip", delivery_email="othello@zulip.com")
    outside = UserProfile.objects.filter(realm__string_id="zephyr", is_bot=False).first()
    assert outside is not None and owner.realm.host == "zulip.testserver"
    assert not SavedSnippet.objects.filter(user_profile__in=[owner, other, outside]).exists()
    data = SavedSnippet.objects.bulk_create(
        [
            SavedSnippet(
                user_profile=owner, realm=owner.realm, title="Pilot owner", content="سلام"
            ),
            SavedSnippet(
                user_profile=other, realm=other.realm, title="Pilot other", content="Private"
            ),
        ]
    )
    keys: list[str] = []

    def basic(user: UserProfile) -> dict[str, str]:
        value = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        return {"Authorization": f"Basic {value}"}

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
        assert store.session_key is not None
        keys.append(store.session_key)
        return {"Cookie": f"{settings.SESSION_COOKIE_NAME}={store.session_key}"}

    host = owner.realm.host

    try:
        browser, browser_other = session(owner), session(other)
        cases = [
            Case("Basic owner", "GET", host, basic(owner), API_PATH, 200, [data[0].to_api_dict()]),
            Case("Session owner", "GET", host, browser, WEB_PATH, 200, [data[0].to_api_dict()]),
            Case("Basic HEAD", "HEAD", host, basic(owner), API_PATH, 200),
            Case("Session HEAD", "HEAD", host, browser, WEB_PATH, 200),
            Case(
                "Basic query",
                "GET",
                host,
                basic(owner),
                API_PATH + "?client=ZulipMobile",
                200,
                [data[0].to_api_dict()],
            ),
            Case(
                "Session query",
                "GET",
                host,
                browser,
                WEB_PATH + "?ignored=pilot",
                200,
                [data[0].to_api_dict()],
            ),
            Case("Basic other", "GET", host, basic(other), API_PATH, 200, [data[1].to_api_dict()]),
            Case(
                "Session other", "GET", host, browser_other, WEB_PATH, 200, [data[1].to_api_dict()]
            ),
            Case("Basic missing", "GET", host, {}, API_PATH, 401),
            Case("Session missing", "GET", host, {}, WEB_PATH, 401),
            Case(
                "Basic unallowlisted realm",
                "GET",
                outside.realm.host,
                basic(outside),
                API_PATH,
                200,
                [],
                False,
            ),
            Case(
                "Session unallowlisted realm",
                "GET",
                outside.realm.host,
                session(outside),
                WEB_PATH,
                200,
                [],
                False,
            ),
            Case(
                "Basic realm mismatch",
                "GET",
                outside.realm.host,
                basic(owner),
                API_PATH,
                401,
                mapped=False,
            ),
            Case(
                "Session realm mismatch",
                "GET",
                outside.realm.host,
                browser,
                WEB_PATH,
                400,
                mapped=False,
            ),
            Case("Basic OPTIONS", "OPTIONS", host, {}, API_PATH, 204, mapped=False),
            Case("Session OPTIONS", "OPTIONS", host, {}, WEB_PATH, 204, mapped=False),
        ]
        maps = ("saved-snippets-map.conf", "saved-snippets-web-map.conf")
        with private_read_proxy(ROOT, directory, args.nginx, args.uwsgi, args.venv, maps) as proxy:

            def request(case: Case) -> tuple[ReadResult, str]:
                return proxy.observe(case.method, case.host, case.headers, case.path)

            def expect(case: Case, upstream: str) -> ReadResult:
                result, peer = request(case)
                assert peer == upstream, (case.label, "wrong upstream", peer, upstream)
                assert result[0] == case.status, (case.label, "status", result[0], case.status)
                if case.method == "HEAD":
                    assert result[1] == b"", case.label
                if case.snippets is not None:
                    assert json.loads(result[1])["saved_snippets"] == case.snippets, case.label
                return result

            def wait_targets(targets: list[tuple[Case, str]]) -> None:
                consecutive = 0

                def loaded() -> bool:
                    nonlocal consecutive
                    if all(request(case)[1] == upstream for case, upstream in targets):
                        consecutive += 1
                    else:
                        consecutive = 0
                    # A first new worker can answer before older workers
                    # finish graceful shutdown after nginx -s reload.
                    return consecutive >= 3

                wait_until(loaded)

            wait_until(lambda: request(cases[0])[0][0] == 200)
            original = [expect(case, proxy.django_address) for case in cases]
            django_metrics = [benchmark(proxy.port, case, args.requests) for case in cases[:2]]
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
                }
            ) as rust_port:
                rust_address = f"127.0.0.1:{rust_port}"
                for case, before in zip(cases[:4], original[:4], strict=True):
                    direct = request_test_http(
                        rust_port,
                        case.method,
                        case.host,
                        case.headers,
                        case.path,
                        include_date=True,
                    )
                    compare(direct, before, case.label)
                proxy.enable_routes(ROUTES, rust_port)
                wait_targets([(case, rust_address) for case in cases[:2]])
                for case, before in zip(cases, original, strict=True):
                    after = expect(case, rust_address if case.mapped else proxy.django_address)
                    compare(after, before, case.label)
                rust_metrics = [benchmark(proxy.port, case, args.requests) for case in cases[:2]]

            def expect_outage(case: Case) -> None:
                failed, peer = request(case)
                assert failed[0] == 502 and peer == rust_address, case.label
                if case.method == "HEAD":
                    assert failed[1] == b"", case.label
                else:
                    assert json.loads(failed[1]) == json.loads(proxy.error_body())

            for case in cases[:4]:
                expect_outage(case)
            for index, name in enumerate(ROUTES):
                proxy.disable_route(name)
                targets = [
                    (case, proxy.django_address if slot <= index else rust_address)
                    for slot, case in enumerate(cases[:2])
                ]
                wait_targets(targets)
                if index == 0:
                    # Removing the API include must not affect the web include.
                    expect_outage(cases[1])
            for case, before in zip(cases, original, strict=True):
                compare(expect(case, proxy.django_address), before, case.label)
            print(
                f"Completed {len(cases)} request cases in each of three routing states, "
                f"4 direct comparisons and 5 outage checks ({proxy.requests} proxy requests including readiness)",
                flush=True,
            )
            for case, python, rust in zip(cases[:2], django_metrics, rust_metrics, strict=True):
                print(
                    f"{case.label}: Django {python[0]:.1f} req/s p95 {python[1]:.2f} ms; "
                    f"Rust {rust[0]:.1f} req/s p95 {rust[1]:.2f} ms",
                    flush=True,
                )
    finally:
        Session.objects.filter(session_key__in=keys).delete()
        SavedSnippet.objects.filter(id__in=[entry.id for entry in data]).delete()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--uwsgi", type=Path, required=True)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--requests", type=int, default=32, choices=range(8, 129), metavar="N")
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if any(name.startswith("ZULIP_RUST_") and name != "ZULIP_RUST_HTTP_BIN" for name in os.environ):
        raise RuntimeError("use a scrubbed environment with only ZULIP_RUST_HTTP_BIN configured")
    validate_fixture(root)
    django.setup()
    verify_private_database()
    with tempfile.TemporaryDirectory(prefix="net.", dir=root) as directory:
        pilot(args, Path(directory))


if __name__ == "__main__":
    main()
