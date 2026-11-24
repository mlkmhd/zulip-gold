# ruff: noqa: INP001
"""Rehearse public and member-only topic history without moving the web alias."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
from base64 import b64encode
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import django
from check_draft_workflow import Actor, actor, request
from check_user_group_details_direct import verify_method_map
from django.utils.timezone import now
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
    wait_until,
)

from zerver.lib.test_rust_http import request_test_http, rust_http_service

if TYPE_CHECKING:
    from zerver.models import Stream, UserProfile

ROOT = Path(__file__).resolve().parents[2]
LOWERCASE = "ZULIP_RUST_CHANNEL_TOPICS_LOWERCASE_FILE"
MAP = "channel-topics-map.conf"
ROUTES = "channel-topics-routes.conf"
PUBLIC = "Pilot topic history"


@dataclass(frozen=True)
class TopicCase(ReadCase):
    topics: tuple[tuple[str, int], ...] | None


def basic(user: UserProfile) -> dict[str, str]:
    token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def make_cases() -> list[TopicCase]:
    from zerver.lib.streams import create_stream_if_needed
    from zerver.models import Message, Stream, Subscription, UserMessage, UserProfile
    from zerver.models.clients import get_client

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = UserProfile.objects.get(realm=member.realm, delivery_email="polonius@zulip.com")
    admin = UserProfile.objects.get(realm=member.realm, delivery_email="iago@zulip.com")
    other = UserProfile.objects.filter(
        realm__string_id="zephyr", is_active=True, is_bot=False
    ).first()
    assert other is not None
    assert guest.is_guest and admin.is_realm_admin
    member.realm.enable_spectator_access = True
    member.realm.save(update_fields=["enable_spectator_access"])
    assert member.realm.web_public_streams_enabled(), (
        "enable spectator access in the private fixture"
    )
    public, _ = create_stream_if_needed(member.realm, PUBLIC)
    web, _ = create_stream_if_needed(member.realm, "Pilot web history", is_web_public=True)
    empty_channel, _ = create_stream_if_needed(member.realm, "Pilot empty history")
    archived, _ = create_stream_if_needed(member.realm, "Pilot archived history")
    Stream.objects.filter(id=archived.id).update(deactivated=True)
    restricted, _ = create_stream_if_needed(
        member.realm,
        "Pilot subscriber history",
        invite_only=True,
        history_public_to_subscribers=False,
    )
    outside, _ = create_stream_if_needed(other.realm, "Pilot other history")
    assert public.is_history_public_to_subscribers()
    assert not restricted.is_history_public_to_subscribers()
    assert restricted.recipient_id is not None
    Subscription.objects.create(
        user_profile=member, recipient_id=restricted.recipient_id, is_user_active=True
    )
    client = get_client("website")

    def message(stream: Stream, topic: str) -> int:
        assert stream.recipient_id is not None
        return Message.objects.create(
            realm_id=stream.realm_id,
            recipient_id=stream.recipient_id,
            sender=member if stream.realm_id == member.realm_id else other,
            sending_client=client,
            date_sent=now(),
            subject=topic,
            content="synthetic pilot fixture",
        ).id

    message(public, "Old topic")
    message(public, "Aς")
    message(public, "İ")
    topic = message(public, "Old Topic")
    literal = message(public, "general chat")
    updated = message(public, "i\u0307")
    greek = message(public, "AΣ")
    empty = message(public, "")
    archived_id = message(archived, "Archived topic")
    web_id = message(web, "Web public")
    visible = message(restricted, "Visible topic")
    UserMessage.objects.create(user_profile=member, message_id=visible)
    message(restricted, "Not in member history")
    outside_id = message(outside, "Other org")
    public_topics = (
        ("general chat", empty),
        ("AΣ", greek),
        ("i\u0307", updated),
        ("general chat", literal),
        ("Old Topic", topic),
    )

    def case(
        label: str,
        actor: UserProfile,
        channel: int | str,
        topics: tuple[tuple[str, int], ...] | None,
        *,
        method: str = "GET",
        status: int | None = None,
        data: list[tuple[str, str]] | None = None,
        language: str | None = None,
        anonymous: bool = False,
        web_alias: bool = False,
        host: str | None = None,
    ) -> TopicCase:
        headers = {} if anonymous else basic(actor)
        if language is not None:
            headers["Accept-Language"] = language
        prefix = "/json" if web_alias else "/api/v1"
        path = f"{prefix}/users/me/{channel}/topics"
        if data:
            path += "?" + urlencode(data)
        mapped = not web_alias and host in {None, "zulip.testserver:8880"}
        mapped = mapped and actor.realm == member.realm and method != "OPTIONS"
        return TopicCase(
            label,
            method,
            host or actor.realm.host,
            headers,
            path,
            status if status is not None else 200 if topics is not None else 400,
            topics,
            mapped=mapped,
            also_compare_direct=not mapped and method == "GET" and not web_alias,
        )

    read = case("public history", member, public.id, public_topics)
    return [
        read,
        replace(read, label="public history HEAD", method="HEAD"),
        case("empty channel", member, empty_channel.id, ()),
        case("member history", member, restricted.id, (("Visible topic", visible),)),
        case("guest public denied", guest, public.id, None),
        case("guest web-public API", guest, web.id, (("Web public", web_id),)),
        case("archived history", member, archived.id, (("Archived topic", archived_id),)),
        case(
            "empty allowed",
            member,
            public.id,
            (("", empty), *public_topics[1:]),
            data=[("allow_empty_topic_name", "true")],
        ),
        case(
            "last query argument",
            member,
            public.id,
            public_topics,
            data=[
                ("allow_empty_topic_name", "true"),
                ("unused_z", "1"),
                ("allow_empty_topic_name", "false"),
                ("client", "ZulipMobile"),
                ("unused_a", "2"),
            ],
        ),
        case("invalid argument", member, public.id, None, data=[("allow_empty_topic_name", "1")]),
        case("invalid JSON", member, public.id, None, data=[("allow_empty_topic_name", "bonjour")]),
        case("admin without private content", admin, restricted.id, None),
        case("cross realm channel", member, outside.id, None),
        case("other realm remains Django", other, outside.id, (("Other org", outside_id),)),
        case("unknown ID", member, 9999999, None),
        case("large unknown ID", member, 10**22, None),
        case("unknown ID French", member, 9999999, None, language="fr"),
        case("unknown ID HEAD", member, 9999999, None, method="HEAD"),
        case("leading zero", member, f"00{public.id}", public_topics),
        case("explicit host port", member, public.id, public_topics, host="zulip.testserver:8880"),
        case("no API authorization", member, web.id, None, status=401, anonymous=True),
        case(
            "anonymous web-public remains Django",
            member,
            web.id,
            (("Web public", web_id),),
            anonymous=True,
            web_alias=True,
        ),
        case(
            "Django OPTIONS", member, public.id, None, method="OPTIONS", status=204, anonymous=True
        ),
    ]


def validate(case: TopicCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method in {"HEAD", "OPTIONS"}:
        assert result[1] == b"", case.label
    elif case.topics is not None:
        assert json.loads(result[1])["topics"] == [
            {"name": name, "max_id": max_id} for name, max_id in case.topics
        ], case.label


def compare(case: TopicCase, actual: ReadResult, expected: ReadResult) -> None:
    validate(case, actual)
    if case.method == "GET":
        assert json.loads(actual[1]) == json.loads(expected[1]), (
            case.label,
            json.loads(actual[1]),
            json.loads(expected[1]),
        )
    else:
        assert actual[1] == expected[1], case.label
    compare_read_headers(actual, expected, case.label)


def exercise_topic_workflow(
    proxy: PrivateReadProxy, owner: Actor, channel_id: int, lowercase: Path
) -> None:
    from zerver.models import Message

    api = f"/api/v1/users/me/{channel_id}/topics"
    web = f"/json/users/me/{channel_id}/topics"
    django_address = proxy.django_address
    first_name = "Original café"
    renamed = "پیگیری café"
    restored_name = "Restored discussion"
    control_name = "Unchanged topic"
    ok = {"result": "success", "msg": ""}

    def ready(upstream: str) -> None:
        consecutive = 0

        def current() -> bool:
            nonlocal consecutive
            for path, headers, expected in (
                (api, owner.basic, upstream),
                (web, owner.web, django_address),
            ):
                reply, route = proxy.observe("GET", owner.host, headers, path)
                if reply[0] != 200 or route != expected:
                    consecutive = 0
                    return False
            consecutive += 1
            return consecutive >= 3

        try:
            wait_until(current)
        except RuntimeError as error:
            observations = []
            for path, headers in ((api, owner.basic), (web, owner.web)):
                retry, route = proxy.observe("GET", owner.host, headers, path)
                observations.append((path, retry[0], route))
            raise RuntimeError(f"topic history did not reach {upstream}: {observations}") from error

    def snapshot(
        path: str, upstream: str, topics: tuple[tuple[str, int], ...]
    ) -> tuple[ReadResult, ReadResult]:
        get = request(proxy, owner, "GET", path, upstream)
        assert json.loads(get[1]) == {
            **ok,
            "topics": [{"name": name, "max_id": id} for name, id in topics],
        }
        head = request(proxy, owner, "HEAD", path, upstream)
        assert head[1] == b""
        return get, head

    def post(topic: str, content: str) -> int:
        result = request(
            proxy,
            owner,
            "POST",
            "/api/v1/messages",
            django_address,
            body=urlencode(
                {"type": "stream", "to": channel_id, "topic": topic, "content": content}
            ),
        )
        body = json.loads(result[1])
        assert body["result"] == "success" and body["msg"] == ""
        id = body["id"]
        assert type(id) is int and id > 0
        return id

    def rename(id: int, name: str) -> None:
        result = request(
            proxy,
            owner,
            "PATCH",
            f"/json/messages/{id}",
            django_address,
            body=urlencode(
                {
                    "topic": name,
                    "propagate_mode": "change_all",
                    "send_notification_to_new_thread": "false",
                    "send_notification_to_old_thread": "false",
                }
            ),
        )
        assert json.loads(result[1]) == {**ok, "detached_uploads": []}

    ready(django_address)
    proxy.assert_untrusted_tls_rejected(owner.host, api)
    snapshot(api, django_address, ())
    snapshot(web, django_address, ())
    first = post(first_name, "First synthetic message.")
    latest = post(first_name, "Second synthetic message.")
    control = post(control_name, "Independent synthetic topic.")
    assert first < latest < control

    def expected(name: str) -> tuple[tuple[str, int], ...]:
        return ((control_name, control), (name, latest))

    def assert_committed(topic: str) -> None:
        assert list(
            Message.objects.filter(id__in=(first, latest, control))
            .order_by("id")
            .values_list("subject", flat=True)
        ) == [topic, topic, control_name]

    baseline = snapshot(api, django_address, expected(first_name))
    snapshot(web, django_address, expected(first_name))
    with rust_http_service({LOWERCASE: str(lowercase)}) as port:
        rust = f"127.0.0.1:{port}"
        proxy.enable_routes((ROUTES,), port, upstreams_per_route=1)
        ready(rust)
        enabled = snapshot(api, rust, expected(first_name))
        for actual, previous in zip(enabled, baseline, strict=True):
            compare_read_headers(actual, previous, "topic history before rename")

        rename(first, renamed)
        assert_committed(renamed)
        snapshot(api, rust, expected(renamed))
        snapshot(web, django_address, expected(renamed))

    failed = request(proxy, owner, "GET", api, rust, status=502)
    assert json.loads(failed[1]) == json.loads(proxy.error_body())
    assert request(proxy, owner, "HEAD", api, rust, status=502)[1] == b""
    snapshot(web, django_address, expected(renamed))

    rename(first, restored_name)
    assert_committed(restored_name)
    during_outage = snapshot(web, django_address, expected(restored_name))
    proxy.disable_route(ROUTES)
    ready(django_address)
    restored_api = snapshot(api, django_address, expected(restored_name))
    restored_web = snapshot(web, django_address, expected(restored_name))
    assert json.loads(restored_api[0][1]) == json.loads(during_outage[0][1])
    for actual, previous in zip(restored_web, during_outage, strict=True):
        compare_read_headers(actual, previous, "web topic history during API rollback")
    assert_committed(restored_name)
    print(f"Topic-history rename and read rollback passed; {proxy.requests} private TLS requests.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    for tool in ("nginx", "uwsgi", "venv", "openssl"):
        parser.add_argument(f"--{tool}", type=Path, required=True)
    parser.add_argument(
        "--workflow", action="store_true", help="rehearse committed topic renames and read rollback"
    )
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("this pilot requires a private loopback-only network namespace")
    if any(name.startswith("ZULIP_RUST_") and name != "ZULIP_RUST_HTTP_BIN" for name in os.environ):
        raise RuntimeError("use a scrubbed environment with only ZULIP_RUST_HTTP_BIN configured")
    validate_fixture(root)
    django.setup()
    verify_private_database()
    from zerver.management.commands.export_rust_alert_word_cache import (
        lowercase_tables,
        publish_json,
    )

    with tempfile.TemporaryDirectory(prefix="channel-topics-", dir=root) as tmp:
        stage = Path(tmp)
        lowercase = stage / "python-lowercase.json"
        publish_json(lowercase, lowercase_tables())
        if args.workflow:
            from zerver.actions.streams import bulk_add_subscriptions
            from zerver.lib.streams import create_stream_if_needed
            from zerver.models import UserProfile

            user = UserProfile.objects.get(
                realm__string_id="zulip", delivery_email="iago@zulip.com"
            )
            assert user.is_realm_admin
            channel, created = create_stream_if_needed(user.realm, f"Workflow {stage.name}")
            assert created
            bulk_add_subscriptions(user.realm, [channel], [user], acting_user=user)
            with (
                actor(user) as owner,
                private_read_proxy(
                    ROOT, stage, args.nginx, args.uwsgi, args.venv, (MAP,), openssl_bin=args.openssl
                ) as proxy,
            ):
                exercise_topic_workflow(proxy, owner, channel.id, lowercase)
            return

        cases = make_cases()
        startup = subprocess.run(
            [os.environ["ZULIP_RUST_HTTP_BIN"], "127.0.0.1:0"],
            env={LOWERCASE: str(lowercase)},
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        assert startup.returncode != 0 and "require ZULIP_RUST_DATABASE_URL" in startup.stderr
        with rust_http_service({}) as port:
            read = cases[0]
            result = request_test_http(port, read.method, read.host, read.headers, read.path)
            assert result[0] == 404, read.label
        verify_method_map(
            args.nginx,
            stage / "methods",
            tuple(cases[:1]),
            map_filename=MAP,
            map_variable="zulip_rust_channel_topics",
        )
        with private_read_proxy(
            ROOT, stage, args.nginx, args.uwsgi, args.venv, (MAP,), openssl_bin=args.openssl
        ) as proxy:
            exercise_paired_read_routes(
                proxy,
                stage,
                ROUTES,
                cases,
                validate=validate,
                compare=compare,
                rust_extra_env={LOWERCASE: str(lowercase)},
                paired_surfaces=1,
                upstreams_per_route=1,
            )


if __name__ == "__main__":
    main()
