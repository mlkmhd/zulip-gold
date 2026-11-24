# ruff: noqa: INP001
"""Rehearse committed reminder writes, optional reads, and rollback privately."""

from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
from pathlib import Path
from time import time
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import django
from check_draft_workflow import Actor, Events, actor, request
from django.conf import settings
from read_activity_fixture import private_activity_broker
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

from zerver.lib.test_rust_http import rust_http_service

if TYPE_CHECKING:
    from zerver.models.scheduled_jobs import APIReminderDirectMessageDict

ROOT = Path(__file__).resolve().parents[2]
API = "/api/v1/reminders"
WEB = "/json/reminders"
MAP = "reminders-map.conf"
ROUTE = "reminders-routes.conf"
OK = {"result": "success", "msg": ""}
TARGET_CONTENT = "Synthetic direct message for the reminder rehearsal."
LATE_NOTE = "Review the original note. فارسی"
EARLY_NOTE = "Revisit this earlier."
OUTAGE_NOTE = "Created while Rust reads were stopped."
RESTORED_NOTE = "Created after restoring Django reads."


def announce(events: Events, event: dict[str, object]) -> None:
    assert events.next() == {
        "event": {"type": "reminders", **event},
        "users": [events.owner.id],
    }
    events.assert_empty()
    events.count += 1


def create(
    proxy: PrivateReadProxy,
    events: Events,
    path: str,
    target: int,
    when: int,
    note: str,
) -> APIReminderDirectMessageDict:
    from zerver.models import ScheduledMessage

    owner = events.owner
    response = request(
        proxy,
        owner,
        "POST",
        path,
        proxy.django_address,
        body=urlencode({"message_id": target, "scheduled_delivery_timestamp": when, "note": note}),
    )
    body = json.loads(response[1])
    reminder_id = body["reminder_id"]
    assert type(reminder_id) is int and reminder_id > 0
    assert body == {**OK, "reminder_id": reminder_id}

    row = ScheduledMessage.objects.get(id=reminder_id, sender_id=owner.id)
    assert row.realm_id == owner.user.realm_id
    assert row.delivery_type == ScheduledMessage.REMIND
    assert not row.delivered and row.delivered_message_id is None and not row.failed
    assert row.reminder_target_message_id == target and row.reminder_note == note
    assert row.scheduled_timestamp.timestamp() == when
    assert note in row.content and note in row.rendered_content
    assert TARGET_CONTENT in row.content and TARGET_CONTENT in row.rendered_content

    expected: APIReminderDirectMessageDict = {
        "reminder_id": reminder_id,
        "to": [owner.id],
        "type": "private",
        "content": row.content,
        "rendered_content": row.rendered_content,
        "scheduled_delivery_timestamp": when,
        "failed": False,
        "reminder_target_message_id": target,
    }
    assert row.to_reminder_dict() == expected
    announce(events, {"op": "add", "reminders": [expected]})
    return expected


def cancel(proxy: PrivateReadProxy, events: Events, reminder_id: int) -> None:
    from zerver.models import ScheduledMessage

    result = request(proxy, events.owner, "DELETE", f"{WEB}/{reminder_id}", proxy.django_address)
    assert json.loads(result[1]) == OK
    assert not ScheduledMessage.objects.filter(id=reminder_id).exists()
    announce(events, {"op": "remove", "reminder_id": reminder_id})


def snapshot(
    proxy: PrivateReadProxy,
    owner: Actor,
    wanted: list[APIReminderDirectMessageDict],
    upstream: str,
) -> tuple[ReadResult, ...]:
    results: list[ReadResult] = []
    for path in (API, WEB):
        actual = request(proxy, owner, "GET", path, upstream)
        assert json.loads(actual[1]) == {**OK, "reminders": wanted}
        head = request(proxy, owner, "HEAD", path, upstream)
        assert head[1] == b""
        results.extend((actual, head))
    return tuple(results)


def same_reads(actual: tuple[ReadResult, ...], prior: tuple[ReadResult, ...]) -> None:
    for index, (now, before) in enumerate(zip(actual, prior, strict=True)):
        assert now[0] == before[0]
        if index % 2 == 0:
            assert json.loads(now[1]) == json.loads(before[1])
        else:
            assert now[1] == before[1]
        compare_read_headers(now, before, "reminder snapshot")


def ready(proxy: PrivateReadProxy, owner: Actor, upstream: str) -> None:
    consecutive = 0

    def current() -> bool:
        nonlocal consecutive
        for path, headers in ((API, owner.basic), (WEB, owner.web)):
            reply, route = proxy.observe("GET", owner.host, headers, path)
            if reply[0] != 200 or route != upstream:
                consecutive = 0
                return False
        consecutive += 1
        return consecutive >= 3

    wait_until(current)


def exercise(
    proxy: PrivateReadProxy, directory: Path, peer: Actor, events: Events, target: int
) -> None:
    from zerver.models import Message, ScheduledMessage

    owner = events.owner
    when = int(time()) + 86_400
    message_count = Message.objects.count()
    django_address = proxy.django_address

    def compose(upstream: str) -> APIReminderDirectMessageDict:
        late = create(proxy, events, API, target, when + 3_600, LATE_NOTE)
        early = create(proxy, events, WEB, target, when, EARLY_NOTE)
        snapshot(proxy, owner, [early, late], upstream)
        snapshot(proxy, peer, [], upstream)
        cancel(proxy, events, early["reminder_id"])
        snapshot(proxy, owner, [late], upstream)
        return late

    ready(proxy, owner, django_address)
    proxy.assert_untrusted_tls_rejected(owner.host, API)
    original = compose(django_address)
    cancel(proxy, events, original["reminder_id"])
    snapshot(proxy, owner, [], django_address)

    secret = directory / "synthetic-secret"
    secret.write_text(settings.SECRET_KEY)
    secret.chmod(0o600)
    with rust_http_service(
        {
            "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
            "ZULIP_RUST_DJANGO_AUTH_BACKENDS": json.dumps(settings.AUTHENTICATION_BACKENDS),
            "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
            "ZULIP_RUST_CLIENT_IP_SOURCE": "proxy",
        }
    ) as port:
        rust = f"127.0.0.1:{port}"
        proxy.enable_routes((ROUTE,), port, upstreams_per_route=2)
        ready(proxy, owner, rust)
        kept = compose(rust)
        kept_id = kept["reminder_id"]
        assert kept_id != original["reminder_id"]
        assert {**kept, "reminder_id": original["reminder_id"]} == original
        live_reads = snapshot(proxy, owner, [kept], rust)

    for path in (API, WEB):
        failed = request(proxy, owner, "GET", path, rust, status=502)
        assert json.loads(failed[1]) == json.loads(proxy.error_body())
        assert request(proxy, owner, "HEAD", path, rust, status=502)[1] == b""
    assert ScheduledMessage.objects.get(id=kept_id).to_reminder_dict() == kept
    events.assert_empty()
    during_outage = create(proxy, events, API, target, when + 7_200, OUTAGE_NOTE)
    cancel(proxy, events, during_outage["reminder_id"])

    proxy.disable_route(ROUTE)
    ready(proxy, owner, django_address)
    same_reads(snapshot(proxy, owner, [kept], django_address), live_reads)
    new = create(proxy, events, WEB, target, when + 7_200, RESTORED_NOTE)
    snapshot(proxy, owner, [kept, new], django_address)
    snapshot(proxy, peer, [], django_address)
    cancel(proxy, events, kept_id)
    cancel(proxy, events, new["reminder_id"])
    snapshot(proxy, owner, [], django_address)
    assert Message.objects.count() == message_count
    assert events.count == 12
    print(
        f"Reminder creation, cancellation, and read rollback passed; "
        f"{events.count} real private owner events and {proxy.requests} TLS requests."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    for name in ("nginx", "uwsgi", "venv", "openssl", "rabbitmq-server", "epmd"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("private loopback only")
    if any(name.startswith("ZULIP_RUST_") and name != "ZULIP_RUST_HTTP_BIN" for name in os.environ):
        raise RuntimeError("use a scrubbed environment with only ZULIP_RUST_HTTP_BIN configured")
    validate_fixture(root)
    django.setup()
    verify_private_database()

    from zerver.actions.message_send import internal_send_private_message
    from zerver.models import ScheduledMessage, UserProfile

    assert settings.TORNADO_PROCESSES == 1, "use one private notification queue"
    owner_user = UserProfile.objects.get(
        realm__string_id="zulip", delivery_email="hamlet@zulip.com"
    )
    peer_user = UserProfile.objects.get(realm=owner_user.realm, delivery_email="cordelia@zulip.com")
    assert not ScheduledMessage.objects.filter(sender__in=(owner_user, peer_user)).exists()
    target = internal_send_private_message(
        peer_user, owner_user, TARGET_CONTENT, disable_external_notifications=True
    )
    assert target is not None

    with (
        tempfile.TemporaryDirectory(prefix="reminder-", dir=root) as tmp,
        actor(owner_user) as owner,
        actor(peer_user) as peer,
    ):
        directory = Path(tmp)
        with (
            private_activity_broker(directory / "broker", args.rabbitmq_server, args.epmd) as (
                broker,
                channel,
            ),
            private_read_proxy(
                ROOT,
                directory,
                args.nginx,
                args.uwsgi,
                args.venv,
                (MAP,),
                openssl_bin=args.openssl,
                activity=broker,
            ) as proxy,
        ):
            exercise(proxy, directory, peer, Events(channel, owner), target)


if __name__ == "__main__":
    main()
