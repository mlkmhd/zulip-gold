# ruff: noqa: INP001
"""Rehearse scheduled-message reads through private nginx and explicit rollback."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from statistics import median

import django
from django.conf import settings
from read_proxy_fixture import (
    PrivateReadProxy,
    ReadResult,
    compare_read_headers,
    private_read_proxy,
    validate_fixture,
    validate_local_root,
    verify_private_database,
    wait_until,
)

from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.lib.timestamp import timestamp_to_datetime

ROOT = Path(__file__).resolve().parents[2]
MAP = "scheduled-messages-map.conf"
ROUTE = "scheduled-messages-routes.conf"
API = "/api/v1/scheduled_messages"
WEB = "/json/scheduled_messages"


@dataclass(frozen=True)
class Case:
    label: str
    method: str
    host: str
    headers: dict[str, str]
    path: str
    status: int
    messages: list[dict[str, object]] | None = None
    mapped: bool = True
    ignored_parameters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.method not in {"GET", "HEAD", "OPTIONS"}:
            raise ValueError("only read-only request methods are allowed")


def validate(case: Case, reply: ReadResult) -> None:
    status, body, _, _ = reply
    assert status == case.status, (case.label, "status", status, case.status)
    if case.method == "HEAD":
        assert body == b"", (case.label, "HEAD body")
    if case.messages is not None:
        document = json.loads(body)
        assert document["scheduled_messages"] == case.messages, (case.label, "rows and ordering")
        assert document.get("ignored_parameters_unsupported", []) == list(case.ignored_parameters)


def compare(case: Case, actual: ReadResult, expected: ReadResult) -> None:
    validate(case, actual)
    assert actual[0] == expected[0], (case.label, "status")
    if case.method == "GET" and case.status == 200:
        # Object key order and whitespace are not part of the JSON contract.
        assert json.loads(actual[1]) == json.loads(expected[1]), (case.label, "JSON")
    else:
        assert actual[1] == expected[1], (case.label, "response body")
    compare_read_headers(actual, expected, case.label)


def benchmark(proxy: PrivateReadProxy, case: Case, count: int) -> tuple[float, float]:
    def one(_: int) -> float:
        start = time.monotonic()
        result = proxy.request(case.method, case.host, case.headers, case.path)
        elapsed = time.monotonic() - start
        validate(case, result)
        return elapsed

    samples, latencies = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, range(4)))
        for _ in range(3):
            start = time.monotonic()
            latencies.extend(pool.map(one, range(count)))
            samples.append(count / (time.monotonic() - start))
    latencies.sort()
    return median(samples), latencies[ceil(0.95 * len(latencies)) - 1] * 1000


def pilot(args: argparse.Namespace, directory: Path, cleanup: ExitStack) -> None:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.models import Message, Recipient, ScheduledMessage, UserProfile
    from zerver.models.clients import get_client
    from zerver.models.recipients import (
        get_direct_message_group,
        get_or_create_direct_message_group,
    )
    from zerver.models.streams import get_stream

    owner = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    other = UserProfile.objects.get(realm__string_id="zulip", delivery_email="othello@zulip.com")
    outside = UserProfile.objects.filter(realm__string_id="zephyr", is_bot=False).first()
    assert outside is not None and owner.realm.host == "zulip.testserver"
    assert not ScheduledMessage.objects.filter(sender__in=[owner, other, outside]).exists()
    stream = get_stream("Denmark", owner.realm)
    assert stream.recipient is not None
    client = get_client("website")

    def group_recipient(ids: list[int]) -> Recipient:
        group = get_direct_message_group(ids)
        if group is None:
            group = get_or_create_direct_message_group(ids)
            assert group.recipient is not None
            cleanup.callback(group.recipient.delete)
            cleanup.callback(group.delete)
        assert group.recipient is not None
        return group.recipient

    def create(
        sender: UserProfile,
        recipient: Recipient,
        content: str,
        timestamp: float,
        *,
        failed: bool = False,
        delivered: bool = False,
    ) -> ScheduledMessage:
        is_channel = recipient == stream.recipient
        row = ScheduledMessage.objects.create(
            realm=sender.realm,
            sender=sender,
            recipient=recipient,
            stream=stream if is_channel else None,
            subject="سلام" if is_channel else Message.DM_TOPIC,
            content=content,
            rendered_content=f"<p>{content}</p>",
            sending_client=client,
            scheduled_timestamp=timestamp_to_datetime(timestamp),
            read_by_sender=True,
            failed=failed,
            delivered=delivered,
            delivery_type=ScheduledMessage.SEND_LATER,
        )
        cleanup.callback(row.delete)
        return row

    direct = group_recipient([owner.id, other.id])
    self_dm = group_recipient([owner.id])
    failed = create(owner, direct, "Failed pilot message", 2, failed=True)
    channel = create(owner, stream.recipient, "پیام کانال", -1.5)
    pending = create(owner, self_dm, "Pending pilot message", 3)
    create(owner, direct, "Delivered pilot message", 1, delivered=True)
    peer = create(other, direct, "Other user's pilot message", 1)
    owner_rows = [channel.to_dict(), failed.to_dict(), pending.to_dict()]
    rows = ScheduledMessage.objects.filter(sender__in=[owner, other, outside]).order_by("id")
    initial_rows = list(rows.values())

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
        cleanup.callback(store.delete)
        assert store.session_key is not None
        return {"Cookie": f"{settings.SESSION_COOKIE_NAME}={store.session_key}"}

    host, outside_host = owner.realm.host, outside.realm.host
    api, browser = basic(owner), session(owner)
    cases = [
        Case("Basic owner", "GET", host, api, API, 200, owner_rows),
        Case("Session owner", "GET", host, browser, WEB, 200, owner_rows),
        Case("Basic HEAD", "HEAD", host, api, API, 200),
        Case("Session HEAD", "HEAD", host, browser, WEB, 200),
        Case(
            "Basic query",
            "GET",
            host,
            api,
            API + "?client=ZulipMobile&unknown=1",
            200,
            owner_rows,
            ignored_parameters=("unknown",),
        ),
        Case(
            "Session query",
            "GET",
            host,
            browser,
            WEB + "?unknown=1",
            200,
            owner_rows,
            ignored_parameters=("unknown",),
        ),
        Case("Basic other owner", "GET", host, basic(other), API, 200, [peer.to_dict()]),
        Case("Session other owner", "GET", host, session(other), WEB, 200, [peer.to_dict()]),
        Case("Basic missing credentials", "GET", host, {}, API, 401),
        Case("Session missing credentials", "GET", host, {}, WEB, 401),
        Case("Basic other realm", "GET", outside_host, basic(outside), API, 200, [], False),
        Case("Session other realm", "GET", outside_host, session(outside), WEB, 200, [], False),
        Case("Basic realm mismatch", "GET", outside_host, api, API, 401, mapped=False),
        Case("Session realm mismatch", "GET", outside_host, browser, WEB, 400, mapped=False),
        Case("Basic OPTIONS", "OPTIONS", host, {}, API, 204, mapped=False),
        Case("Session OPTIONS", "OPTIONS", host, {}, WEB, 204, mapped=False),
        Case("Basic detail GET", "GET", host, api, f"{API}/{pending.id}", 405, mapped=False),
        Case("Session detail GET", "GET", host, browser, f"{WEB}/{pending.id}", 405, mapped=False),
    ]

    with private_read_proxy(
        ROOT, directory, args.nginx, args.uwsgi, args.venv, (MAP,), openssl_bin=args.openssl
    ) as proxy:

        def request(case: Case) -> tuple[ReadResult, str]:
            return proxy.observe(case.method, case.host, case.headers, case.path)

        def expect(case: Case, upstream: str) -> ReadResult:
            result, actual = request(case)
            assert actual == upstream, (case.label, "wrong upstream", actual, upstream)
            validate(case, result)
            return result

        def wait_for_routes(upstream: str) -> None:
            consecutive = 0

            def ready() -> bool:
                nonlocal consecutive
                if all(request(case)[1] == upstream for case in cases[:2]):
                    consecutive += 1
                else:
                    consecutive = 0
                # Graceful reload can briefly leave old and new workers active.
                return consecutive >= 3

            wait_until(ready)

        wait_until(lambda: request(cases[0])[0][0] == 200)
        proxy.assert_untrusted_tls_rejected(cases[0].host, cases[0].path)
        original = [expect(case, proxy.django_address) for case in cases]
        django_metrics = [benchmark(proxy, case, args.requests) for case in cases[:2]]
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
            for case, before in zip(cases, original, strict=True):
                if case.mapped:
                    direct_result = request_test_http(
                        rust_port,
                        case.method,
                        case.host,
                        case.headers,
                        case.path,
                        include_date=True,
                    )
                    compare(case, direct_result, before)
            proxy.enable_routes((ROUTE,), rust_port, upstreams_per_route=2)
            wait_for_routes(rust_address)
            for case, before in zip(cases, original, strict=True):
                target = rust_address if case.mapped else proxy.django_address
                compare(case, expect(case, target), before)
            rust_metrics = [benchmark(proxy, case, args.requests) for case in cases[:2]]

        for case in cases[:4]:
            failed_result, upstream = request(case)
            assert failed_result[0] == 502 and upstream == rust_address, case.label
            if case.method == "HEAD":
                assert failed_result[1] == b"", case.label
            else:
                assert json.loads(failed_result[1]) == json.loads(proxy.error_body()), case.label
        for case in cases:
            if not case.mapped:
                expect(case, proxy.django_address)

        proxy.disable_route(ROUTE)
        wait_for_routes(proxy.django_address)
        for case, before in zip(cases, original, strict=True):
            compare(case, expect(case, proxy.django_address), before)
        assert list(rows.values()) == initial_rows, "the read rehearsal changed scheduled rows"
        count = sum(case.mapped for case in cases)
        print(
            f"{len(cases)} cases in each of three routing states, {count} direct comparisons, "
            f"4 outage and {len(cases) - count} unaffected Django checks while Rust is stopped "
            f"({proxy.requests} private proxy requests including readiness)",
            flush=True,
        )
        for case, django_speed, rust_speed in zip(
            cases[:2], django_metrics, rust_metrics, strict=True
        ):
            print(
                f"{case.label}: Django {django_speed[0]:.1f} req/s p95 {django_speed[1]:.2f} ms; "
                f"Rust {rust_speed[0]:.1f} req/s p95 {rust_speed[1]:.2f} ms",
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument(
        "--openssl", type=Path, help="enable private loopback TLS with this OpenSSL"
    )
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
    with tempfile.TemporaryDirectory(prefix="net.", dir=root) as directory, ExitStack() as cleanup:
        pilot(args, Path(directory), cleanup)


if __name__ == "__main__":
    main()
