# ruff: noqa: INP001
"""Rehearse private draft composition and rollback with real broker events."""

from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
from base64 import b64encode
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast
from urllib.parse import urlencode

import django
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.utils.crypto import get_random_string
from read_activity_fixture import private_activity_broker, rust_activity_environment
from read_proxy_fixture import (
    PrivateActivity,
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
    from pika.adapters.blocking_connection import BlockingChannel

    from zerver.models import UserProfile

ROOT = Path(__file__).resolve().parents[2]
MAP = "drafts-patch-map.conf"
ROUTES = "drafts-routes.conf"
API = "/api/v1/drafts"
WEB = "/json/drafts"
QUEUE = "notify_tornado"
INITIAL = "First synthetic draft."
EDITED = "Edited synthetic draft."
OK = {"result": "success", "msg": ""}


@dataclass(frozen=True)
class Actor:
    id: int
    host: str
    basic: dict[str, str] = field(repr=False)
    web: dict[str, str] = field(repr=False)


class DraftState(TypedDict):
    id: int
    type: str
    to: list[int]
    topic: str
    content: str
    timestamp: int


class DraftList(TypedDict):
    result: str
    msg: str
    count: int
    drafts: list[DraftState]


@contextmanager
def actor(user: UserProfile) -> Iterator[Actor]:
    session = SessionStore()
    session.update(
        {
            "_auth_user_id": str(user.id),
            "_auth_user_backend": "zproject.backends.DevAuthBackend",
            "_auth_user_hash": user.get_session_auth_hash(),
        }
    )
    session.create()
    token = get_random_string(32)
    authorization = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
    try:
        yield Actor(
            user.id,
            user.realm.host,
            {"Authorization": f"Basic {authorization}"},
            {
                "Cookie": (
                    f"{settings.SESSION_COOKIE_NAME}={session.session_key}; "
                    f"{settings.CSRF_COOKIE_NAME}={token}"
                ),
                "X-CSRFToken": token,
                "Origin": f"https://{user.realm.host}",
            },
        )
    finally:
        session.delete()


class Events:
    def __init__(self, channel: BlockingChannel, owner: Actor) -> None:
        self.channel = channel
        self.owner = owner
        self.count = 0
        row = channel.queue_declare(QUEUE, durable=True)
        assert row.method.message_count == row.method.consumer_count == 0

    def next(self) -> dict[str, object]:
        notice: dict[str, object] | None = None

        def receive() -> bool:
            nonlocal notice
            method, properties, body = self.channel.basic_get(QUEUE, auto_ack=False)
            if method is None:
                return False
            assert properties is not None and body is not None and properties.delivery_mode == 2
            assert not method.redelivered
            notice = json.loads(body)
            assert method.delivery_tag is not None
            self.channel.basic_ack(method.delivery_tag)
            return True

        wait_until(receive, timeout=4)
        assert notice is not None
        return notice

    def draft(self, event: dict[str, object]) -> None:
        assert self.next() == {"event": {"type": "drafts", **event}, "users": [self.owner.id]}
        self.assert_empty()
        self.count += 1

    def assert_empty(self) -> None:
        status = self.channel.queue_declare(QUEUE, passive=True)
        assert status.method.message_count == 0, "unexpected extra notification"

    def sent(self, message_id: int, peer: Actor) -> None:
        notice = self.next()
        event = cast(dict[str, object], notice["event"])
        assert event["type"] == "message", "send must not publish a draft deletion"
        # This is the broker envelope before Tornado expands the message.
        assert event["message"] == message_id
        users = cast(list[dict[str, object]], notice["users"])
        assert {user["id"] for user in users} == {self.owner.id, peer.id}
        self.assert_empty()


def request(
    proxy: PrivateReadProxy,
    actor: Actor,
    method: str,
    path: str,
    upstream: str,
    *,
    body: str | None = None,
    status: int = 200,
) -> ReadResult:
    headers = actor.basic if path.startswith("/api/") else actor.web
    if body is not None:
        headers = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
    response, actual = proxy.observe(method, actor.host, headers, path, body=body)
    assert (response[0], actual) == (status, upstream), (method, path, response[0])
    return response


def stored(id: int, owner: Actor, *, timestamp: float | None = None) -> DraftState:
    from zerver.models import Draft

    row = Draft.objects.get(id=id, user_profile_id=owner.id)
    if timestamp is not None:
        assert row.last_edit_time.timestamp() == timestamp
    return cast(DraftState, row.to_dict())


def snapshots(
    proxy: PrivateReadProxy, actor: Actor, wanted: list[DraftState], upstream: str
) -> tuple[ReadResult, ...]:
    replies: list[ReadResult] = []
    for path in (API, WEB):
        reply = request(proxy, actor, "GET", path, upstream)
        assert json.loads(reply[1]) == {
            **OK,
            "count": len(wanted),
            "drafts": wanted,
        }, "owner's complete draft snapshot"
        head = request(proxy, actor, "HEAD", path, upstream)
        assert head[1] == b""
        replies.extend((reply, head))
    return tuple(replies)


def compare(
    actual: tuple[ReadResult, ...], expected: tuple[ReadResult, ...], *, same_id: bool
) -> None:
    def normalized(body: bytes) -> DraftList:
        data = cast(DraftList, json.loads(body))
        if not same_id:
            for item in data["drafts"]:
                item["id"] = 0
        return data

    for index, (result, original) in enumerate(zip(actual, expected, strict=True)):
        assert result[0] == original[0]
        if index % 2 == 0:
            assert normalized(result[1]) == normalized(original[1])
        else:
            assert result[1] == original[1]
        compare_read_headers(result, original, "draft snapshot")


def compose(
    proxy: PrivateReadProxy, owner: Actor, peer: Actor, upstream: str, events: Events
) -> DraftState:
    created_at = 1234.25
    edited_at = 1235.5
    initial = {
        "type": "private",
        "to": [peer.id],
        "topic": "",
        "content": INITIAL,
        "timestamp": created_at,
    }
    created = request(
        proxy, owner, "POST", API, upstream, body=urlencode({"drafts": json.dumps([initial])})
    )
    [id] = json.loads(created[1])["ids"]
    assert json.loads(created[1]) == {**OK, "ids": [id]}
    expected = stored(id, owner, timestamp=created_at)
    assert expected == {"id": id, **initial, "timestamp": int(created_at)}
    events.draft({"op": "add", "drafts": [expected]})
    snapshots(proxy, owner, [expected], upstream)

    edited = {**initial, "content": EDITED, "timestamp": edited_at}
    result = request(
        proxy,
        owner,
        "PATCH",
        f"{WEB}/{id}",
        upstream,
        body=urlencode({"draft": json.dumps(edited)}),
    )
    assert json.loads(result[1]) == OK
    expected = stored(id, owner, timestamp=edited_at)
    assert expected == {"id": id, **edited, "timestamp": int(edited_at)}
    events.draft({"op": "update", "draft": expected})
    snapshots(proxy, peer, [], upstream)
    return expected


def send_and_clear(
    proxy: PrivateReadProxy,
    owner: Actor,
    peer: Actor,
    draft: DraftState,
    upstream: str,
    events: Events,
) -> None:
    from zerver.models import Draft, Message

    sent = request(
        proxy,
        owner,
        "POST",
        "/api/v1/messages",
        proxy.django_address,
        body=urlencode(
            {"type": "direct", "to": json.dumps([peer.id]), "content": draft["content"]}
        ),
    )
    message_id = json.loads(sent[1])["id"]
    assert Message.objects.get(id=message_id, sender_id=owner.id).content == draft["content"]
    events.sent(message_id, peer)
    assert stored(draft["id"], owner) == draft, "sending does not itself clear the draft"
    result = request(proxy, owner, "DELETE", f"{WEB}/{draft['id']}", upstream)
    assert json.loads(result[1]) == OK
    assert not Draft.objects.filter(id=draft["id"]).exists()
    events.draft({"op": "remove", "draft_id": draft["id"]})
    snapshots(proxy, owner, [], upstream)


def exercise(
    proxy: PrivateReadProxy,
    directory: Path,
    owner: Actor,
    peer: Actor,
    broker: PrivateActivity,
    channel: BlockingChannel,
) -> None:
    events = Events(channel, owner)

    def wait_for_routes(upstream: str) -> None:
        consecutive = 0

        def ready() -> bool:
            nonlocal consecutive
            for path, headers in ((API, owner.basic), (WEB, owner.web)):
                reply, actual = proxy.observe("GET", owner.host, headers, path)
                if reply[0] != 200 or actual != upstream:
                    consecutive = 0
                    return False
            # Old and new workers can both answer during a graceful reload.
            consecutive += 1
            return consecutive >= 3

        wait_until(ready)

    wait_for_routes(proxy.django_address)
    proxy.assert_untrusted_tls_rejected(owner.host, API)
    first = compose(proxy, owner, peer, proxy.django_address, events)
    original = snapshots(proxy, owner, [first], proxy.django_address)
    send_and_clear(proxy, owner, peer, first, proxy.django_address, events)

    secret = directory / "synthetic-secret"
    secret.write_text(settings.SECRET_KEY)
    secret.chmod(0o600)
    with rust_http_service(
        {
            **rust_activity_environment(broker),
            "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
            "ZULIP_RUST_DJANGO_AUTH_BACKENDS": json.dumps(settings.AUTHENTICATION_BACKENDS),
            "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
            "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
            "ZULIP_RUST_WEB_POST_SCHEME": "https",
            "ZULIP_RUST_MAX_MESSAGE_LENGTH": str(settings.MAX_MESSAGE_LENGTH),
            "ZULIP_RUST_CROSS_REALM_BOT_EMAILS": json.dumps(
                sorted(settings.CROSS_REALM_BOT_EMAILS)
            ),
            "ZULIP_RUST_DRAFT_POSTS": "1",
            "ZULIP_RUST_DRAFT_PATCHES": "1",
            "ZULIP_RUST_DRAFT_DELETES": "1",
        }
    ) as rust_port:
        rust = f"127.0.0.1:{rust_port}"
        proxy.enable_routes((ROUTES,), rust_port, upstreams_per_route=2)
        wait_for_routes(rust)
        second = compose(proxy, owner, peer, rust, events)
        compare(snapshots(proxy, owner, [second], rust), original, same_id=False)
        send_and_clear(proxy, owner, peer, second, rust, events)

        retained = compose(proxy, owner, peer, rust, events)
        retained_results = snapshots(proxy, owner, [retained], rust)

    for path in (API, WEB):
        failed = request(proxy, owner, "GET", path, rust, status=502)
        assert json.loads(failed[1]) == json.loads(proxy.error_body())
    assert stored(retained["id"], owner) == retained
    events.assert_empty()
    proxy.disable_route(ROUTES)
    wait_for_routes(proxy.django_address)
    compare(
        snapshots(proxy, owner, [retained], proxy.django_address),
        retained_results,
        same_id=True,
    )
    send_and_clear(proxy, owner, peer, retained, proxy.django_address, events)
    assert events.count == 9
    print(
        "Django, Rust, and explicit rollback draft composition passed; "
        f"{events.count} real private owner events and {proxy.requests} TLS requests."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    for tool in ("nginx", "uwsgi", "venv", "openssl", "rabbitmq-server", "epmd"):
        parser.add_argument(f"--{tool}", type=Path, required=True)
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

    from zerver.models import Draft, UserProfile

    assert settings.TORNADO_PROCESSES == 1, "use one private notification queue"
    owner = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    peer = UserProfile.objects.get(realm=owner.realm, delivery_email="cordelia@zulip.com")
    assert not Draft.objects.filter(user_profile__in=(owner, peer)).exists()
    UserProfile.objects.filter(id__in=(owner.id, peer.id)).update(
        enable_drafts_synchronization=True
    )

    with (
        tempfile.TemporaryDirectory(prefix="draft-flow-", dir=root) as tmp,
        actor(owner) as owner_actor,
        actor(peer) as peer_actor,
    ):
        directory = Path(tmp)
        with (
            private_activity_broker(
                directory / "broker",
                args.rabbitmq_server,
                args.epmd,
            ) as (broker, channel),
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
            exercise(proxy, directory, owner_actor, peer_actor, broker, channel)


if __name__ == "__main__":
    main()
