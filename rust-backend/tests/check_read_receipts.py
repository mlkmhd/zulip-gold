# ruff: noqa: INP001
"""Rehearse read-only Basic and session message read receipts in a private TLS proxy."""

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
from pathlib import Path
from typing import TYPE_CHECKING

import django
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
    from zerver.models import Message, UserProfile

ROOT = Path(__file__).resolve().parents[2]
PILOT = "ZULIP_RUST_READ_RECEIPTS_READ_PILOT"
MAP = "read-receipts-map.conf"
ROUTES = "read-receipts-routes.conf"


@dataclass(frozen=True)
class ReceiptCase(ReadCase):
    user_ids: tuple[int, ...] | None
    message: str | None


def seed_accounting_message(member: UserProfile) -> Message:
    from zerver.lib.streams import create_stream_if_needed
    from zerver.models import Message, Realm
    from zerver.models.clients import get_client

    Realm.objects.filter(id=member.realm_id).update(enable_read_receipts=True)
    stream, _ = create_stream_if_needed(member.realm, "Read receipt pilot public")
    assert stream.recipient_id is not None
    message, _ = Message.objects.get_or_create(
        recipient_id=stream.recipient_id,
        sender=member,
        realm_id=member.realm_id,
        content="Synthetic read receipt pilot",
        defaults={
            "date_sent": timezone_now(),
            "sending_client": get_client("website"),
            "is_channel_message": True,
            "rendered_content": "<p>Synthetic read receipt pilot</p>",
        },
    )
    return message


def make_cases(cleanup: ExitStack) -> list[ReceiptCase]:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.actions.user_groups import check_add_user_group
    from zerver.lib.streams import create_stream_if_needed
    from zerver.models import (
        Message,
        MutedUser,
        NamedUserGroup,
        Realm,
        Stream,
        Subscription,
        UserMessage,
        UserProfile,
    )
    from zerver.models.clients import get_client
    from zerver.models.groups import SystemGroups
    from zerver.models.recipients import get_or_create_direct_message_group

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = UserProfile.objects.get(realm=member.realm, delivery_email="polonius@zulip.com")
    admin = UserProfile.objects.get(realm=member.realm, delivery_email="iago@zulip.com")
    hidden = UserProfile.objects.get(realm=member.realm, delivery_email="othello@zulip.com")
    target = UserProfile.objects.get(realm=member.realm, delivery_email="prospero@zulip.com")
    inactive = UserProfile.objects.get(realm=member.realm, delivery_email="cordelia@zulip.com")
    bot = UserProfile.objects.get(realm=member.realm, delivery_email="default-bot@zulip.com")
    foreign = UserProfile.objects.filter(
        realm__string_id="zephyr", role=UserProfile.ROLE_MEMBER, is_active=True, is_bot=False
    ).first()
    assert foreign is not None and guest.is_guest and admin.is_realm_admin and bot.is_bot
    nobody = NamedUserGroup.objects.get(
        realm_for_sharding=member.realm, is_system_group=True, name=SystemGroups.NOBODY
    )
    UserProfile.objects.filter(id=hidden.id).update(send_read_receipts=False)
    UserProfile.objects.filter(id=inactive.id).update(is_active=False)
    Realm.objects.filter(id=foreign.realm_id).update(enable_read_receipts=False)
    MutedUser.objects.get_or_create(user_profile=member, muted_user=admin)
    MutedUser.objects.get_or_create(user_profile=target, muted_user=member)

    def stream(
        label: str,
        *,
        is_web_public: bool = False,
        invite_only: bool = False,
        history_public_to_subscribers: bool | None = None,
        can_subscribe_group: NamedUserGroup | None = None,
        can_add_subscribers_group: NamedUserGroup | None = None,
    ) -> Stream:
        result, _ = create_stream_if_needed(
            member.realm,
            f"Read receipt pilot {label}",
            is_web_public=is_web_public,
            invite_only=invite_only,
            history_public_to_subscribers=history_public_to_subscribers,
            can_subscribe_group=can_subscribe_group,
            can_add_subscribers_group=can_add_subscribers_group,
        )
        return result

    def message(sender: UserProfile, recipient_id: int | None, channel: bool = True) -> Message:
        assert recipient_id is not None
        return Message.objects.create(
            sender=sender,
            realm_id=sender.realm_id,
            recipient_id=recipient_id,
            date_sent=timezone_now(),
            content="Synthetic read receipt test message",
            is_channel_message=channel,
            sending_client=get_client("website"),
        )

    def receipt(value: Message, reader: UserProfile, flags: int) -> None:
        UserMessage.objects.create(message=value, user_profile=reader, flags=flags)

    def subscribe(channel: Stream, user: UserProfile, *, active: bool = True) -> None:
        assert channel.recipient_id is not None
        Subscription.objects.create(
            recipient_id=channel.recipient_id,
            user_profile=user,
            active=active,
            is_user_active=user.is_active,
        )

    public_message = seed_accounting_message(member)
    public = Stream.objects.get(realm=member.realm, name="Read receipt pilot public")
    assert public_message.recipient_id == public.recipient_id
    public_flags_message = message(admin, public.recipient_id)
    read = 1 << UserMessage.flags.read.number
    historical = 1 << UserMessage.flags.historical.number
    assert read == 1
    for user, flags in (
        (admin, read),
        (member, read),
        (bot, read | historical),
        (target, read),
        (hidden, read),
        (inactive, read),
        (guest, 0),
    ):
        receipt(public_flags_message, user, flags)

    public_mutes_message = message(bot, public.recipient_id)
    for user in (member, admin, target):
        receipt(public_mutes_message, user, read)

    # Web-public channel browsing isn't an independent grant for message IDs.
    web = stream("web", is_web_public=True)
    web_message = message(target, web.recipient_id)
    subscribe(web, target)

    private = stream(
        "private",
        invite_only=True,
        history_public_to_subscribers=False,
        can_subscribe_group=nobody,
        can_add_subscribers_group=nobody,
    )
    subscribe(private, member)
    subscribe(private, guest, active=False)
    private_old = message(admin, private.recipient_id)
    private_recent = message(admin, private.recipient_id)
    receipt(private_old, guest, read)
    receipt(private_recent, member, read)

    inner = check_add_user_group(member.realm, "Receipt pilot inner", [member], acting_user=admin)
    nested = check_add_user_group(member.realm, "Receipt pilot nested", [], acting_user=admin)
    nested.direct_subgroups.add(inner)
    history = stream(
        "shared history",
        invite_only=True,
        history_public_to_subscribers=True,
        can_subscribe_group=nobody,
        can_add_subscribers_group=nested,
    )
    history_message = message(admin, history.recipient_id)
    Stream.objects.filter(id=history.id).update(deactivated=True)
    assert history.history_public_to_subscribers

    group = get_or_create_direct_message_group([member.id, admin.id, target.id])
    direct = message(admin, group.recipient_id, False)
    receipt(direct, member, read)
    receipt(direct, target, read)
    personal_group = get_or_create_direct_message_group([member.id, hidden.id])
    personal = message(hidden, personal_group.recipient_id, False)
    receipt(personal, member, read)
    foreign_channel, _ = create_stream_if_needed(foreign.realm, "Receipt pilot other realm")
    foreign_message = message(foreign, foreign_channel.recipient_id)

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

    invalid = "Invalid message(s)"
    disabled = "Read receipts are disabled in this organization."

    def case(
        mode: str,
        label: str,
        actor: UserProfile,
        msg: int | str,
        users: tuple[int, ...] | None = None,
        *,
        error: str | None = None,
        status: int | None = None,
        method: str = "GET",
        suffix: str = "",
        language: str | None = None,
        anonymous: bool = False,
        host: str | None = None,
        path_within_message: str = "/read_receipts",
    ) -> ReceiptCase:
        prefix = "/api/v1" if mode == "basic" else "/json"
        auth = {} if anonymous else headers(mode, actor)
        if language:
            auth["Accept-Language"] = language
        mapped = (
            actor.realm == member.realm
            and method != "OPTIONS"
            and path_within_message == "/read_receipts"
        )
        return ReceiptCase(
            f"{mode} {label}",
            method,
            host or actor.realm.host,
            auth,
            f"{prefix}/messages/{msg}{path_within_message}{suffix}",
            status if status is not None else 200 if users is not None else 400,
            users,
            error,
            mapped=mapped,
            also_compare_direct=not mapped and actor.realm != member.realm and method == "GET",
        )

    modes = ("basic", "session")
    reads = [
        case(
            mode,
            "public read and historical bot",
            member,
            public_flags_message.id,
            (member.id, bot.id),
        )
        for mode in modes
    ]
    cases = [*reads, *(replace(read, label=f"{read.label} HEAD", method="HEAD") for read in reads)]
    for mode in modes:
        cases.extend(
            (
                case(
                    mode,
                    "member public history without subscription",
                    hidden,
                    public_flags_message.id,
                    (member.id, bot.id, target.id),
                ),
                case(
                    mode,
                    "sender and mutes in either direction",
                    member,
                    public_mutes_message.id,
                    (member.id,),
                ),
                case(
                    mode,
                    "guest cannot read unsubscribed public message",
                    guest,
                    public_message.id,
                    error=invalid,
                ),
                case(
                    mode,
                    "guest cannot read unsubscribed web public message",
                    guest,
                    web_message.id,
                    error=invalid,
                ),
                case(
                    mode,
                    "private subscriber without prior receipt",
                    member,
                    private_old.id,
                    error=invalid,
                ),
                case(
                    mode,
                    "former private subscriber with receipt",
                    guest,
                    private_old.id,
                    error=invalid,
                ),
                case(mode, "private subscribed recipient", member, private_recent.id, (member.id,)),
                case(
                    mode,
                    "admin cannot read unreceived private message",
                    admin,
                    private_recent.id,
                    error=invalid,
                ),
                case(
                    mode,
                    "nested group sees archived shared history",
                    member,
                    history_message.id,
                    (),
                ),
                case(mode, "group direct recipient", member, direct.id, (member.id,)),
                case(mode, "personal direct recipient", member, personal.id, (member.id,)),
                case(mode, "another direct conversation", hidden, direct.id, error=invalid),
                case(mode, "different realm", member, foreign_message.id, error=invalid),
                case(
                    mode, "other host with setting off", foreign, foreign_message.id, error=disabled
                ),
                case(
                    mode,
                    "access error precedes disabled setting",
                    foreign,
                    public_message.id,
                    error=invalid,
                ),
                case(mode, "unknown message", member, 9999999, error=invalid),
                case(mode, "large unknown message", member, 10**22, error=invalid),
                case(mode, "unknown message French", member, 9999999, language="fr"),
                case(mode, "private denied HEAD", admin, private_recent.id, method="HEAD"),
                case(
                    mode,
                    "path and ignored fields",
                    member,
                    f"00{private_recent.id}",
                    (member.id,),
                    suffix=f"?client=website&message_id={history_message.id}&other=1",
                ),
                case(
                    mode,
                    "allowlisted port",
                    member,
                    public_message.id,
                    (),
                    host="zulip.testserver:8880",
                ),
                case(
                    mode, "no authorization", member, public_message.id, status=401, anonymous=True
                ),
                case(
                    mode,
                    "Django OPTIONS",
                    member,
                    public_message.id,
                    status=204,
                    method="OPTIONS",
                    anonymous=True,
                ),
                case(
                    mode,
                    "Django message detail remains separate",
                    member,
                    public_message.id,
                    status=200,
                    path_within_message="",
                ),
            )
        )
    return cases


def validate(case: ReceiptCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method == "HEAD":
        assert result[1] == b"", case.label
    elif case.user_ids is not None:
        assert sorted(json.loads(result[1])["user_ids"]) == sorted(case.user_ids), case.label
    elif case.message is not None:
        assert json.loads(result[1])["msg"] == case.message, case.label


def compare(case: ReceiptCase, actual: ReadResult, expected: ReadResult) -> None:
    validate(case, actual)
    if case.method == "GET":
        got, want = json.loads(actual[1]), json.loads(expected[1])
        if case.user_ids is not None:
            got["user_ids"].sort()
            want["user_ids"].sort()
        assert got == want, (case.label, got, want)
    else:
        assert actual[1] == expected[1], case.label
    compare_read_headers(actual, expected, case.label)


def startup_guards(stage: Path, reads: list[ReceiptCase]) -> None:
    db = connection.settings_dict
    assert not db["PASSWORD"]
    dsn = make_dsn(dbname=db["NAME"], host=db["HOST"], user=db["USER"], port=db["PORT"])
    for extra, error in (
        ({}, "Read receipts read pilot requires ZULIP_RUST_DATABASE_URL"),
        (
            {"ZULIP_RUST_DATABASE_URL": dsn, "ZULIP_RUST_EXTERNAL_HOST": settings.EXTERNAL_HOST},
            "Read receipts read pilot requires signed Django sessions",
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
        tempfile.TemporaryDirectory(prefix="read-receipts-", dir=root) as tmp,
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
            map_variable="zulip_rust_read_receipts",
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
