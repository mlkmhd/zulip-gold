# ruff: noqa: INP001
"""Rehearse read-only Basic and session channel details in a private TLS proxy."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
from base64 import b64encode
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import django
from check_subscription_status import make_cases as seed_subscriptions
from check_user_group_details_direct import verify_method_map
from django.conf import settings
from django.db import connection
from django.utils.timezone import now as timezone_now
from psycopg2.extensions import make_dsn
from read_proxy_fixture import (
    ReadCase,
    ReadResult,
    compare_read_headers,
    exercise_paired_read_routes,
    private_read_proxy,
    validate_fixture,
    validate_local_root,
    verify_private_database,
)

from zerver.lib.test_rust_http import request_test_http, rust_http_service

if TYPE_CHECKING:
    from zerver.models import UserProfile

ROOT = Path(__file__).resolve().parents[2]
PILOT = "ZULIP_RUST_CHANNEL_DETAILS_READ_PILOT"
MAP = "channel-details-map.conf"
ROUTES = "channel-details-routes.conf"


@dataclass(frozen=True)
class ChannelCase(ReadCase):
    payload: dict[str, object] | None
    message: str | None


def make_cases(cleanup: ExitStack) -> list[ChannelCase]:
    from django.contrib.sessions.backends.db import SessionStore

    from analytics.models import StreamCount
    from zerver.models import (
        ChannelFolder,
        GroupGroupMembership,
        NamedUserGroup,
        Stream,
        UserGroup,
        UserGroupMembership,
        UserProfile,
    )
    from zerver.models.groups import SystemGroups
    from zerver.models.streams import StreamTopicsPolicyEnum

    seed_subscriptions(cleanup)
    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = UserProfile.objects.get(realm=member.realm, delivery_email="polonius@zulip.com")
    admin = UserProfile.objects.get(realm=member.realm, delivery_email="iago@zulip.com")
    target = UserProfile.objects.get(realm=member.realm, delivery_email="prospero@zulip.com")
    inactive = UserProfile.objects.get(realm=member.realm, delivery_email="cordelia@zulip.com")
    bot = UserProfile.objects.get(realm=member.realm, delivery_email="default-bot@zulip.com")
    foreign = UserProfile.objects.filter(
        realm__string_id="zephyr", role=UserProfile.ROLE_MEMBER, is_active=True, is_bot=False
    ).first()
    assert foreign is not None and guest.is_guest and not inactive.is_active and bot.is_bot

    def channel(name: str) -> Stream:
        return Stream.objects.get(realm=member.realm, name=name)

    public = channel("Pilot subscription status")
    private = channel("Pilot private subscriptions")
    web = channel("Pilot web subscriptions")
    archived = channel("Pilot archived subscriptions")
    metadata = list(
        Stream.objects.filter(realm=member.realm, name__startswith="Pilot metadata can_")
    )
    foreign_channel = Stream.objects.get(realm=foreign.realm, name="Pilot foreign subscription")
    assert len(metadata) == 3 and archived.deactivated
    roles = {
        name: NamedUserGroup.objects.get(
            realm_for_sharding=member.realm, name=name, is_system_group=True
        )
        for name in (
            SystemGroups.ADMINISTRATORS,
            SystemGroups.MODERATORS,
            SystemGroups.FULL_MEMBERS,
        )
    }
    anon = UserGroup.objects.create(realm=member.realm)
    for user in (target, inactive, member):
        UserGroupMembership.objects.create(user_group=anon, user_profile=user)
    GroupGroupMembership.objects.create(supergroup=anon, subgroup=roles[SystemGroups.MODERATORS])
    folder = ChannelFolder.objects.create(realm=member.realm, name="Pilot folder", creator=member)
    now = timezone_now()
    image_key = UUID("e4266834-2f84-4082-b024-f5e1656d94cc")
    Stream.objects.filter(id=public.id).update(
        date_created=now - timedelta(days=45),
        creator=member,
        folder=folder,
        description="فارسی and English",
        rendered_description="<p>فارسی and English</p>",
        default_push_notifications=True,
        message_retention_days=23,
        is_recently_active=False,
        icon_key=image_key,
        subscriber_count=2,
        can_delete_own_message_group=anon,
        can_send_message_group=roles[SystemGroups.ADMINISTRATORS],
        topics_policy=StreamTopicsPolicyEnum.empty_topic_only.value,
    )
    Stream.objects.filter(id=web.id).update(
        date_created=now - timedelta(days=14, hours=1),
        can_send_message_group=roles[SystemGroups.MODERATORS],
        topics_policy=StreamTopicsPolicyEnum.allow_empty_topic.value,
    )
    Stream.objects.filter(id=private.id).update(
        can_send_message_group=roles[SystemGroups.FULL_MEMBERS],
        topics_policy=StreamTopicsPolicyEnum.disable_empty_topic.value,
    )
    Stream.objects.filter(id=archived.id).update(date_created=now - timedelta(days=30))
    for stream, days, value, subgroup in (
        (public, 3, 360, "false"),
        (public, 5, 140, "true"),
        (public, 29, 2000, "false"),
        (web, 6, 99, "false"),
        (archived, 4, 1, "false"),
    ):
        StreamCount.objects.create(
            stream=stream,
            realm=member.realm,
            property="messages_in_stream:is_bot:day",
            end_time=now - timedelta(days=days),
            value=value,
            subgroup=subgroup,
        )

    public_payload = {
        "stream_id": public.id,
        "date_created": int((now - timedelta(days=45)).timestamp()),
        "icon_url": f"/json/streams/{public.id}/icon?v={image_key}",
        "stream_weekly_traffic": 120,
        "creator_id": member.id,
        "folder_id": folder.id,
        "description": "فارسی and English",
        "is_recently_active": False,
        "message_retention_days": 23,
        "stream_post_policy": 2,
        "is_announcement_only": True,
        "topics_policy": "empty_topic_only",
        "can_send_message_group": roles[SystemGroups.ADMINISTRATORS].id,
        "can_delete_own_message_group": {
            "direct_members": sorted((member.id, target.id)),
            "direct_subgroups": [roles[SystemGroups.MODERATORS].id],
        },
    }
    private_payload = {
        "stream_id": private.id,
        "invite_only": True,
        "stream_weekly_traffic": None,
        "stream_post_policy": 3,
        "topics_policy": "disable_empty_topic",
    }
    web_payload = {
        "stream_id": web.id,
        "is_web_public": True,
        "stream_weekly_traffic": 49,
        "stream_post_policy": 4,
        "topics_policy": "allow_empty_topic",
    }
    archived_payload = {
        "stream_id": archived.id,
        "is_archived": True,
        "stream_weekly_traffic": 1,
    }
    sessions: dict[int, str] = {}

    def headers(mode: str, actor: UserProfile) -> dict[str, str]:
        if mode == "basic":
            token = b64encode(f"{actor.delivery_email}:{actor.api_key}".encode()).decode()
            return {"Authorization": f"Basic {token}"}
        if actor.id not in sessions:
            session = SessionStore()
            session.update(
                {
                    "_auth_user_id": str(actor.id),
                    "_auth_user_backend": "zproject.backends.DevAuthBackend",
                    "_auth_user_hash": actor.get_session_auth_hash(),
                }
            )
            session.create()
            cleanup.callback(session.delete)
            assert session.session_key is not None
            sessions[actor.id] = session.session_key
        return {"Cookie": f"{settings.SESSION_COOKIE_NAME}={sessions[actor.id]}"}

    def case(
        mode: str,
        label: str,
        actor: UserProfile,
        channel_id: int | str,
        payload: dict[str, object] | None = None,
        *,
        error: str | None = None,
        status: int | None = None,
        method: str = "GET",
        suffix: str = "",
        language: str | None = None,
        anonymous: bool = False,
        host: str | None = None,
    ) -> ChannelCase:
        prefix = "/api/v1" if mode == "basic" else "/json"
        auth = {} if anonymous else headers(mode, actor)
        if language:
            auth["Accept-Language"] = language
        path = f"{prefix}/streams/{channel_id}{suffix}"
        mapped = actor.realm == member.realm and method != "OPTIONS" and not suffix.startswith("/")
        return ChannelCase(
            f"{mode} {label}",
            method,
            host or actor.realm.host,
            auth,
            path,
            status if status is not None else 200 if payload is not None else 400,
            payload,
            error,
            mapped=mapped,
            also_compare_direct=not mapped and actor.realm != member.realm and method == "GET",
        )

    modes = ("basic", "session")
    reads = [case(mode, "public projection", member, public.id, public_payload) for mode in modes]
    cases = [*reads, *(replace(read, label=f"{read.label} HEAD", method="HEAD") for read in reads)]
    for mode in modes:
        cases.extend(
            (
                case(mode, "administrator private metadata", admin, private.id, private_payload),
                case(mode, "subscribed guest private metadata", guest, private.id, private_payload),
                case(mode, "guest web public metadata", guest, web.id, web_payload),
                case(
                    mode,
                    "guest private organization metadata denied",
                    guest,
                    public.id,
                    error="Invalid channel ID",
                ),
                case(
                    mode,
                    "member private metadata denied",
                    member,
                    private.id,
                    error="Invalid channel ID",
                ),
                case(mode, "archived details", member, archived.id, archived_payload),
                *(
                    case(
                        mode,
                        f"configured {stream.name}",
                        member,
                        stream.id,
                        {"stream_id": stream.id},
                    )
                    for stream in metadata
                ),
                case(
                    mode,
                    "foreign channel rejected",
                    member,
                    foreign_channel.id,
                    error="Invalid channel ID",
                ),
                case(
                    mode,
                    "other host remains Django",
                    foreign,
                    foreign_channel.id,
                    {"stream_id": foreign_channel.id},
                ),
                case(mode, "unknown channel", member, 9999999, error="Invalid channel ID"),
                case(mode, "large unknown channel", member, 10**22, error="Invalid channel ID"),
                case(mode, "unknown channel French", member, 9999999, language="fr"),
                case(mode, "private denied HEAD", member, private.id, method="HEAD"),
                case(
                    mode,
                    "path-only and query reporting",
                    member,
                    f"00{public.id}",
                    public_payload,
                    suffix=f"?client=website&stream_id={private.id}&other=1",
                ),
                case(
                    mode,
                    "allowlisted port",
                    member,
                    public.id,
                    public_payload,
                    host="zulip.testserver:8880",
                ),
                case(mode, "no authorization", member, public.id, status=401, anonymous=True),
                case(
                    mode,
                    "Django OPTIONS",
                    member,
                    public.id,
                    status=204,
                    method="OPTIONS",
                    anonymous=True,
                ),
                case(
                    mode,
                    "Django roster remains separate",
                    member,
                    public.id,
                    status=200,
                    suffix="/members",
                ),
            )
        )
    cases.append(case("basic", "bot reads visible details", bot, public.id, public_payload))
    return cases


def validate(case: ChannelCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method in {"HEAD", "OPTIONS"}:
        assert result[1] == b"", case.label
    elif case.payload is not None:
        stream = json.loads(result[1])["stream"]
        assert "email_token" not in stream, case.label
        assert {key: stream[key] for key in case.payload} == case.payload, (case.label, stream)
    elif case.message is not None:
        assert json.loads(result[1])["msg"] == case.message, case.label


def compare(case: ChannelCase, actual: ReadResult, expected: ReadResult) -> None:
    validate(case, actual)
    if case.method == "GET":
        got, want = json.loads(actual[1]), json.loads(expected[1])
        if case.path.endswith("/members"):
            got["subscribers"].sort()
            want["subscribers"].sort()
        assert got == want, (case.label, got, want)
    else:
        assert actual[1] == expected[1], case.label
    compare_read_headers(actual, expected, case.label)


def startup_guards(stage: Path, reads: list[ChannelCase]) -> None:
    db = connection.settings_dict
    assert not db["PASSWORD"]
    dsn = make_dsn(dbname=db["NAME"], host=db["HOST"], user=db["USER"], port=db["PORT"])
    for extra, error in (
        ({}, "Channel details read pilot requires ZULIP_RUST_DATABASE_URL"),
        (
            {"ZULIP_RUST_DATABASE_URL": dsn, "ZULIP_RUST_EXTERNAL_HOST": settings.EXTERNAL_HOST},
            "Channel details read pilot requires signed Django sessions",
        ),
    ):
        process = subprocess.run(
            [os.environ["ZULIP_RUST_HTTP_BIN"], "127.0.0.1:0"],
            env={PILOT: "1", **extra},
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        assert process.returncode != 0 and error in process.stderr
    secret = stage / "synthetic-signing-input"
    secret.write_text(settings.SECRET_KEY)
    secret.chmod(0o600)
    with rust_http_service(
        {
            "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
            "ZULIP_RUST_DJANGO_AUTH_BACKENDS": json.dumps(settings.AUTHENTICATION_BACKENDS),
            "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
            "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
        }
    ) as port:
        for case in reads:
            result = request_test_http(port, "GET", case.host, case.headers, case.path)
            assert result[0] == 404, case.label


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    for tool in ("nginx", "uwsgi", "venv", "openssl"):
        parser.add_argument(f"--{tool}", type=Path, required=True)
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
    with (
        tempfile.TemporaryDirectory(prefix="channel-details-", dir=root) as tmp,
        ExitStack() as cleanup,
    ):
        stage = Path(tmp)
        cases = make_cases(cleanup)
        startup_guards(stage, cases[:2])
        verify_method_map(
            args.nginx,
            stage / "methods",
            tuple(cases[:2]),
            map_filename=MAP,
            map_variable="zulip_rust_channel_details",
            non_read_methods=("POST", "PATCH", "DELETE", "OPTIONS"),
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
                rust_extra_env={PILOT: "1"},
            )


if __name__ == "__main__":
    main()
