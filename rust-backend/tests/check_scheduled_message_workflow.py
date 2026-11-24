# ruff: noqa: INP001
"""Rehearse scheduled-message composition, read routing, and rollback privately."""

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
    from zerver.models import UserProfile
    from zerver.models.scheduled_jobs import APIScheduledDirectMessageDict

ROOT = Path(__file__).resolve().parents[2]
API = "/api/v1/scheduled_messages"
WEB = "/json/scheduled_messages"
MAP = "scheduled-messages-map.conf"
ROUTE = "scheduled-messages-routes.conf"
OK = {"result": "success", "msg": ""}
INITIAL = "First synthetic scheduled message."
EDITED = "Edited synthetic scheduled message."
RETAINED = "Rescheduled after returning to Django."


def announce(events: Events, event: dict[str, object]) -> None:
    assert events.next() == {
        "event": {"type": "scheduled_messages", **event},
        "users": [events.owner.id],
    }
    events.assert_empty()
    events.count += 1


def card(
    id: int, owner: Actor, peer: Actor, content: str, when: int
) -> APIScheduledDirectMessageDict:
    from zerver.models import ScheduledMessage

    row = ScheduledMessage.objects.get(id=id, sender_id=owner.id)
    assert row.realm_id == owner.user.realm_id
    assert row.delivery_type == ScheduledMessage.SEND_LATER and not row.delivered
    expected: APIScheduledDirectMessageDict = {
        "scheduled_message_id": id,
        "to": [peer.id],
        "type": "private",
        "content": content,
        "rendered_content": f"<p>{content}</p>",
        "scheduled_delivery_timestamp": when,
        "failed": False,
    }
    assert row.to_dict() == expected
    return expected


def snapshot(
    proxy: PrivateReadProxy,
    owner: Actor,
    wanted: list[APIScheduledDirectMessageDict],
    upstream: str,
) -> tuple[ReadResult, ...]:
    results: list[ReadResult] = []
    for path in (API, WEB):
        actual = request(proxy, owner, "GET", path, upstream)
        assert json.loads(actual[1]) == {**OK, "scheduled_messages": wanted}
        head = request(proxy, owner, "HEAD", path, upstream)
        assert head[1] == b""
        results.extend((actual, head))
    return tuple(results)


def same_card(
    actual: tuple[ReadResult, ...], prior: tuple[ReadResult, ...], *, same_id: bool
) -> None:
    for index, (now, before) in enumerate(zip(actual, prior, strict=True)):
        assert now[0] == before[0]
        if index % 2 == 0:
            after_body, before_body = json.loads(now[1]), json.loads(before[1])
            if not same_id:
                for data in (after_body, before_body):
                    [item] = data["scheduled_messages"]
                    item["scheduled_message_id"] = 0
            assert after_body == before_body
        else:
            assert now[1] == before[1]
        if same_id:
            compare_read_headers(now, before, "scheduled-message snapshot")


def create(
    proxy: PrivateReadProxy, owner: Actor, peer: Actor, events: Events, when: int
) -> APIScheduledDirectMessageDict:
    result = request(
        proxy,
        owner,
        "POST",
        API,
        proxy.django_address,
        body=urlencode(
            {
                "type": "direct",
                "to": json.dumps([peer.id]),
                "content": INITIAL,
                "scheduled_delivery_timestamp": when,
            }
        ),
    )
    id = json.loads(result[1])["scheduled_message_id"]
    assert type(id) is int and id > 0
    assert json.loads(result[1]) == {**OK, "scheduled_message_id": id}
    result_card = card(id, owner, peer, INITIAL, when)
    announce(events, {"op": "add", "scheduled_messages": [result_card]})
    return result_card


def edit(
    proxy: PrivateReadProxy,
    owner: Actor,
    peer: Actor,
    events: Events,
    id: int,
    content: str,
    when: int,
) -> APIScheduledDirectMessageDict:
    result = request(
        proxy,
        owner,
        "PATCH",
        f"{WEB}/{id}",
        proxy.django_address,
        body=urlencode({"content": content, "scheduled_delivery_timestamp": when}),
    )
    assert json.loads(result[1]) == OK
    result_card = card(id, owner, peer, content, when)
    announce(events, {"op": "update", "scheduled_message": result_card})
    return result_card


def cancel(proxy: PrivateReadProxy, owner: Actor, events: Events, id: int) -> None:
    from zerver.models import ScheduledMessage

    result = request(proxy, owner, "DELETE", f"{WEB}/{id}", proxy.django_address)
    assert json.loads(result[1]) == OK
    assert not ScheduledMessage.objects.filter(id=id).exists()
    announce(events, {"op": "remove", "scheduled_message_id": id})


def exercise(
    proxy: PrivateReadProxy, directory: Path, owner: Actor, peer: Actor, events: Events
) -> None:
    when = int(time()) + 86_400
    django_address = proxy.django_address

    def ready(upstream: str) -> None:
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

    def compose(upstream: str) -> tuple[APIScheduledDirectMessageDict, tuple[ReadResult, ...]]:
        initial = create(proxy, owner, peer, events, when)
        snapshot(proxy, owner, [initial], upstream)
        id = initial["scheduled_message_id"]
        revised = edit(proxy, owner, peer, events, id, EDITED, when + 3_600)
        snapshot(proxy, peer, [], upstream)
        return revised, snapshot(proxy, owner, [revised], upstream)

    ready(django_address)
    proxy.assert_untrusted_tls_rejected(owner.host, API)
    original, response = compose(django_address)
    cancel(proxy, owner, events, original["scheduled_message_id"])
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
        ready(rust)
        revised, responses = compose(rust)
        same_card(responses, response, same_id=False)
        cancel(proxy, owner, events, revised["scheduled_message_id"])
        snapshot(proxy, owner, [], rust)
        kept, original_kept_responses = compose(rust)

    for path in (API, WEB):
        failed = request(proxy, owner, "GET", path, rust, status=502)
        assert json.loads(failed[1]) == json.loads(proxy.error_body())
        assert request(proxy, owner, "HEAD", path, rust, status=502)[1] == b""
    kept_id = kept["scheduled_message_id"]
    assert card(kept_id, owner, peer, EDITED, when + 3_600) == kept
    events.assert_empty()

    proxy.disable_route(ROUTE)
    ready(django_address)
    restored = snapshot(proxy, owner, [kept], django_address)
    same_card(restored, original_kept_responses, same_id=True)
    latest = edit(proxy, owner, peer, events, kept_id, RETAINED, when + 7_200)
    snapshot(proxy, owner, [latest], django_address)
    cancel(proxy, owner, events, kept_id)
    snapshot(proxy, owner, [], django_address)
    assert events.count == 10
    print(
        "Scheduled-message composition and rollback passed; "
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

    from zerver.models import ScheduledMessage, UserProfile

    assert settings.TORNADO_PROCESSES == 1, "use one private notification queue"
    owner_user: UserProfile = UserProfile.objects.get(
        realm__string_id="zulip", delivery_email="hamlet@zulip.com"
    )
    peer_user: UserProfile = UserProfile.objects.get(
        realm=owner_user.realm, delivery_email="cordelia@zulip.com"
    )
    assert not ScheduledMessage.objects.filter(sender__in=(owner_user, peer_user)).exists()

    with (
        tempfile.TemporaryDirectory(prefix="schedule-", dir=root) as tmp,
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
            exercise(proxy, directory, owner, peer, Events(channel, owner))


if __name__ == "__main__":
    main()
