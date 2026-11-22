# ruff: noqa: INP001
"""Rehearse scheduled-message or reminder reads over private nginx with rollback."""

from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
import time
from base64 import b64encode
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, replace
from math import ceil
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zerver.models import UserProfile

import django
from django.conf import settings
from read_proxy_fixture import (
    PrivateReadProxy,
    ReadCase,
    ReadResult,
    compare_read_headers,
    exercise_paired_read_routes,
    private_read_proxy,
    validate_fixture,
    validate_local_root,
    verify_private_database,
)

from zerver.lib.timestamp import timestamp_to_datetime

ROOT = Path(__file__).resolve().parents[2]
MAP = "scheduled-messages-map.conf"
ROUTE = "scheduled-messages-routes.conf"
API = "/api/v1/scheduled_messages"
WEB = "/json/scheduled_messages"
REMINDER_MAP = "reminders-map.conf"
REMINDER_ROUTE = "reminders-routes.conf"
REMINDER_API = "/api/v1/reminders"
REMINDER_WEB = "/json/reminders"


@dataclass(frozen=True)
class Case(ReadCase):
    messages: Sequence[Mapping[str, object]] | None = None
    ignored_parameters: tuple[str, ...] = ()
    result_key: str = "scheduled_messages"


def validate(case: Case, reply: ReadResult) -> None:
    status, body, _, _ = reply
    assert status == case.status, (case.label, "status", status, case.status)
    if case.method == "HEAD":
        assert body == b"", (case.label, "HEAD body")
    if case.messages is not None:
        document = json.loads(body)
        assert document[case.result_key] == case.messages, (case.label, "rows and ordering")
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


def seed_reminder_cards(
    owner: UserProfile, peers: tuple[UserProfile, ...], cleanup: ExitStack
) -> dict[int, list[dict[str, object]]]:
    """Populate only disposable fixture rows and return the exact expected API cards."""
    from django.utils.timezone import now

    from zerver.models import Message, ScheduledMessage
    from zerver.models.clients import get_client
    from zerver.models.recipients import (
        get_direct_message_group,
        get_or_create_direct_message_group,
    )

    assert not ScheduledMessage.objects.filter(sender__in=(owner, *peers)).exists()
    client = get_client("website")
    cards: dict[int, list[dict[str, object]]] = {}
    for user in (owner, *peers):
        group = get_direct_message_group([user.id])
        if group is None:
            group = get_or_create_direct_message_group(id_list=[user.id])
            assert group.recipient is not None
            cleanup.callback(group.recipient.delete)
            cleanup.callback(group.delete)
        assert group.recipient is not None
        target = Message.objects.create(
            realm=user.realm,
            sender=user,
            recipient=group.recipient,
            date_sent=now(),
            subject=Message.DM_TOPIC,
            content="Reminder fixture target",
            rendered_content="<p>Reminder fixture target</p>",
            is_channel_message=False,
            sending_client=client,
        )
        cleanup.callback(target.delete)

        jobs: list[tuple[str, float, int, bool, bool]] = [
            ("Pending یادآوری", 104.75, ScheduledMessage.REMIND, False, False)
        ]
        if user == owner:
            jobs.extend(
                [
                    ("Failed reminder", 101.5, ScheduledMessage.REMIND, True, False),
                    ("Delivered reminder", 100, ScheduledMessage.REMIND, False, True),
                    (
                        "Scheduled message, not a reminder",
                        102,
                        ScheduledMessage.SEND_LATER,
                        False,
                        False,
                    ),
                ]
            )
        expected = []
        for content, timestamp, kind, failed, delivered in jobs:
            rendered_content = f"<p>{content}</p>"
            target_id = target.id if kind == ScheduledMessage.REMIND else None
            row = ScheduledMessage.objects.create(
                realm=user.realm,
                sender=user,
                recipient=group.recipient,
                subject=Message.DM_TOPIC,
                content=content,
                rendered_content=rendered_content,
                sending_client=client,
                scheduled_timestamp=timestamp_to_datetime(timestamp),
                read_by_sender=kind == ScheduledMessage.SEND_LATER,
                failed=failed,
                delivered=delivered,
                delivery_type=kind,
                reminder_target_message_id=target_id,
                reminder_note="Fixture note" if kind == ScheduledMessage.REMIND else None,
                failure_message="Fixture delivery failure" if failed else None,
            )
            cleanup.callback(row.delete)
            if kind == ScheduledMessage.REMIND and not delivered:
                expected.append(
                    {
                        "reminder_id": row.id,
                        "to": [user.id],
                        "type": "private",
                        "content": content,
                        "rendered_content": rendered_content,
                        "scheduled_delivery_timestamp": int(timestamp),
                        "failed": failed,
                        "reminder_target_message_id": target_id,
                    }
                )
        # Insert the owner pending-first, but assert the earlier failed card arrives first.
        cards[user.id] = list(reversed(expected))
    return cards


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
    owner_rows: Sequence[Mapping[str, object]]
    peer_rows: Sequence[Mapping[str, object]]
    outside_rows: Sequence[Mapping[str, object]]
    if args.reminders:
        map_file, route_file = REMINDER_MAP, REMINDER_ROUTE
        api_path, web_path = REMINDER_API, REMINDER_WEB
        result_key = "reminders"
        cards = seed_reminder_cards(owner, (other, outside), cleanup)
        owner_rows = cards[owner.id]
        peer_rows = cards[other.id]
        outside_rows = cards[outside.id]
        detail_id = cards[owner.id][-1]["reminder_id"]
    else:
        map_file, route_file = MAP, ROUTE
        api_path, web_path = API, WEB
        result_key = "scheduled_messages"
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
        peer_rows = [peer.to_dict()]
        outside_rows = []
        detail_id = pending.id
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
        Case("Basic owner", "GET", host, api, api_path, 200, owner_rows),
        Case("Session owner", "GET", host, browser, web_path, 200, owner_rows),
        Case("Basic HEAD", "HEAD", host, api, api_path, 200),
        Case("Session HEAD", "HEAD", host, browser, web_path, 200),
        Case(
            "Basic query",
            "GET",
            host,
            api,
            api_path + "?client=ZulipMobile&unknown=1",
            200,
            owner_rows,
            ignored_parameters=("unknown",),
        ),
        Case(
            "Session query",
            "GET",
            host,
            browser,
            web_path + "?unknown=1",
            200,
            owner_rows,
            ignored_parameters=("unknown",),
        ),
        Case("Basic other owner", "GET", host, basic(other), api_path, 200, peer_rows),
        Case("Session other owner", "GET", host, session(other), web_path, 200, peer_rows),
        Case("Basic missing credentials", "GET", host, {}, api_path, 401),
        Case("Session missing credentials", "GET", host, {}, web_path, 401),
        Case(
            "Basic other realm",
            "GET",
            outside_host,
            basic(outside),
            api_path,
            200,
            outside_rows,
            mapped=False,
        ),
        Case(
            "Session other realm",
            "GET",
            outside_host,
            session(outside),
            web_path,
            200,
            outside_rows,
            mapped=False,
        ),
        Case("Basic realm mismatch", "GET", outside_host, api, api_path, 401, mapped=False),
        Case("Session realm mismatch", "GET", outside_host, browser, web_path, 400, mapped=False),
        Case("Basic OPTIONS", "OPTIONS", host, {}, api_path, 204, mapped=False),
        Case("Session OPTIONS", "OPTIONS", host, {}, web_path, 204, mapped=False),
        Case("Basic detail GET", "GET", host, api, f"{api_path}/{detail_id}", 405, mapped=False),
        Case(
            "Session detail GET", "GET", host, browser, f"{web_path}/{detail_id}", 405, mapped=False
        ),
    ]

    cases = [replace(case, result_key=result_key) for case in cases]
    if args.reminders:
        from check_user_group_details_direct import verify_method_map

        verify_method_map(
            args.nginx,
            directory / "reminder-methods",
            tuple(cases[:2]),
            map_filename=map_file,
            map_variable="zulip_rust_reminders",
            non_read_methods=("POST", "PATCH", "DELETE", "OPTIONS"),
        )
    with private_read_proxy(
        ROOT, directory, args.nginx, args.uwsgi, args.venv, (map_file,), openssl_bin=args.openssl
    ) as proxy:
        exercise_paired_read_routes(
            proxy,
            directory,
            route_file,
            cases,
            validate=validate,
            compare=compare,
            measure=None
            if args.reminders
            else lambda proxy, case: benchmark(proxy, case, args.requests),
        )
    assert list(rows.values()) == initial_rows, (
        "the read rehearsal changed scheduled or reminder rows"
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
    parser.add_argument("--reminders", action="store_true", help="rehearse reminder reads instead")
    args = parser.parse_args()
    if args.reminders and args.openssl is None:
        parser.error("reminder rehearsal requires --openssl")
    if args.reminders and socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("reminder rehearsal requires a private loopback-only network namespace")
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
