# ruff: noqa: INP001
"""Compare the default-off in-process subscription batch with Django."""

from __future__ import annotations

import argparse
import json
import socket
from base64 import b64encode
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

import django
from django.test import Client, override_settings
from read_proxy_fixture import validate_fixture, validate_local_root, verify_private_database

if TYPE_CHECKING:
    from zerver.models import UserProfile


def response(
    actor: UserProfile, browser: bool, mode: str | None
) -> tuple[int, bytes, tuple[str, ...]]:
    client = Client()
    headers = {}
    if browser:
        client.force_login(actor, backend="zproject.backends.DevAuthBackend")
        prefix = "/json"
    else:
        token = b64encode(f"{actor.delivery_email}:{actor.api_key}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
        prefix = "/api/v1"
    result = client.get(
        prefix + "/users/me/subscriptions",
        {} if mode is None else {"include_subscribers": mode},
        HTTP_HOST=actor.realm.host,
        headers=headers,
        secure=True,
    )
    header_values = tuple(
        result.headers.get(header, "")
        for header in (
            "Content-Type",
            "Content-Length",
            "Content-Language",
            "Cache-Control",
            "Vary",
        )
    )
    return result.status_code, result.content, header_values


def run_parity() -> None:
    from _zulip_backend import assemble_own_subscription_batch

    from zerver.lib import native_channel_payload
    from zerver.lib.streams import create_stream_if_needed
    from zerver.models import NamedUserGroup, Stream, Subscription, UserProfile
    from zerver.models.groups import SystemGroups

    assert native_channel_payload.own_subscriptions_available()
    assert native_channel_payload._backend is None
    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")

    def user(name: str) -> UserProfile:
        return UserProfile.objects.get(realm=member.realm, delivery_email=f"{name}@zulip.com")

    guest = user("polonius")
    idle = user("othello")
    bot = user("default-bot")
    gone = user("cordelia")
    former = user("prospero")
    foreign = UserProfile.objects.filter(
        realm__string_id="zephyr", is_active=True, is_bot=False
    ).first()
    assert guest.is_guest and bot.is_bot and foreign is not None
    UserProfile.objects.filter(id__in=(idle.id, bot.id)).update(long_term_idle=True)
    UserProfile.objects.filter(id=gone.id).update(is_active=False)
    nobody = NamedUserGroup.objects.get(
        realm_for_sharding=member.realm, is_system_group=True, name=SystemGroups.NOBODY
    )
    small, _ = create_stream_if_needed(member.realm, "Subscription Rust test A e\u0301")
    large, _ = create_stream_if_needed(member.realm, "Subscription Rust test فارسی")
    private, _ = create_stream_if_needed(
        member.realm,
        "Subscription Rust test private",
        invite_only=True,
        history_public_to_subscribers=False,
        can_subscribe_group=nobody,
        can_add_subscribers_group=nobody,
    )
    archived, _ = create_stream_if_needed(member.realm, "Subscription Rust test archived")
    former_channel, _ = create_stream_if_needed(member.realm, "Subscription Rust test former")

    def enroll(
        channel: Stream, actor: UserProfile, *, active: bool = True, user_active: bool = True
    ) -> Subscription:
        return Subscription.objects.create(
            recipient_id=channel.recipient_id,
            user_profile=actor,
            active=active,
            is_user_active=user_active,
        )

    own = enroll(small, member)
    Subscription.objects.filter(id=own.id).update(
        is_muted=True,
        pin_to_top=True,
        color="#00ff00",
        desktop_notifications=None,
        email_notifications=False,
    )
    enroll(small, idle)
    for actor in (member, guest, idle, bot):
        enroll(large, actor)
    enroll(large, gone, user_active=False)
    enroll(large, former, active=False)
    for actor in (member, guest):
        enroll(private, actor)
    enroll(archived, member)
    enroll(former_channel, member, active=False)
    Stream.objects.filter(id=small.id).update(subscriber_count=2)
    Stream.objects.filter(id=large.id).update(subscriber_count=4)
    Stream.objects.filter(id=private.id).update(subscriber_count=2)
    Stream.objects.filter(id=archived.id).update(deactivated=True)

    calls: list[tuple[bool, int | None]] = []

    def tracked(
        rows: list[native_channel_payload.OwnSubscriptionInput],
        partial: bool,
        threshold: int | None,
    ) -> tuple[list[object], list[int]] | None:
        calls.append((partial, threshold))
        result = assemble_own_subscription_batch(rows, partial, threshold)
        if threshold is not None and threshold > 2**63 - 1:
            assert result is None
        else:
            assert result is not None
        return result

    def decline(
        rows: list[native_channel_payload.OwnSubscriptionInput],
        partial: bool,
        threshold: int | None,
    ) -> None:
        calls.append((partial, threshold))

    def read_with(
        backend: object, actor: UserProfile, browser: bool, mode: str | None
    ) -> tuple[int, bytes, tuple[str, ...]]:
        with patch.object(native_channel_payload, "_own_backend", backend):
            return response(actor, browser, mode)

    correct = SimpleNamespace(assemble_own_subscription_batch=tracked)
    unsupported = SimpleNamespace(assemble_own_subscription_batch=decline)
    old = SimpleNamespace()
    cases = 0
    for threshold in (1, 4, 1000):
        with override_settings(MIN_PARTIAL_SUBSCRIBERS_CHANNEL_SIZE=threshold):
            for actor in (member, guest, bot, foreign):
                for mode in (None, "false", "true", "partial"):
                    partial = mode == "partial"
                    invocation = [(partial, threshold if partial else None)]
                    for browser in (False, True):
                        calls.clear()
                        reference = read_with(None, actor, browser, mode)
                        assert reference[0] == 200 and not calls
                        assert read_with(correct, actor, browser, mode) == reference
                        assert calls == invocation
                        calls.clear()
                        assert read_with(unsupported, actor, browser, mode) == reference
                        assert calls == invocation
                        calls.clear()
                        assert read_with(old, actor, browser, mode) == reference and not calls
                        data = json.loads(reference[1])["subscriptions"]
                        mapped = {entry["stream_id"]: entry for entry in data}
                        assert archived.id not in mapped and former_channel.id not in mapped
                        assert all("email_token" not in entry for entry in data)
                        if actor.id == member.id:
                            channel = mapped[small.id]
                            assert (
                                channel["is_muted"]
                                and not channel["in_home_view"]
                                and channel["pin_to_top"]
                            )
                            assert (
                                channel["desktop_notifications"] is None
                                and channel["email_notifications"] is False
                            )
                        if actor.id == guest.id:
                            assert small.id not in mapped and private.id in mapped
                        if actor.realm_id != member.realm_id:
                            assert small.id not in mapped and private.id not in mapped
                        if actor.id in (member.id, guest.id, bot.id):
                            channel = mapped[large.id]
                            if mode in (None, "false"):
                                assert (
                                    "subscribers" not in channel
                                    and "partial_subscribers" not in channel
                                )
                            elif mode == "true" or threshold > 4:
                                assert channel["subscribers"] == sorted(
                                    (member.id, guest.id, idle.id, bot.id)
                                )
                            else:
                                assert channel["partial_subscribers"] == sorted(
                                    (member.id, guest.id, bot.id)
                                )
                                assert "subscribers" not in channel
                        cases += 1
    for browser in (False, True):
        calls.clear()
        invalid = read_with(None, member, browser, "unknown")
        assert invalid[0] == 400
        assert read_with(correct, member, browser, "unknown") == invalid and not calls
        with override_settings(MIN_PARTIAL_SUBSCRIBERS_CHANNEL_SIZE=2**80):
            reference = read_with(None, member, browser, "partial")
            assert reference[0] == 200
            assert read_with(correct, member, browser, "partial") == reference
            assert calls == [(True, 2**80)]
    assert cases == 96
    print("96 cases each: Python, native, extension without export, and declining batch.")
    print("Invalid option and oversized-threshold fallback also matched on both surfaces.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("the parity check requires an isolated loopback namespace")
    validate_fixture(root)
    django.setup()
    verify_private_database()
    run_parity()


if __name__ == "__main__":
    main()
