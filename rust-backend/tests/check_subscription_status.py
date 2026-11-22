# ruff: noqa: INP001
"""Rehearse read-only Basic and session subscription flag in a private TLS proxy."""

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
PILOT = "ZULIP_RUST_SUBSCRIPTION_STATUS_READ_PILOT"
MAP = "subscription-status-map.conf"
ROUTES = "subscription-status-routes.conf"


@dataclass(frozen=True)
class SubscriptionCase(ReadCase):
    subscribed: bool | None
    message: str | None


def make_cases(cleanup: ExitStack) -> list[SubscriptionCase]:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.actions.user_groups import check_add_user_group
    from zerver.lib.streams import create_stream_if_needed
    from zerver.models import NamedUserGroup, Stream, Subscription, UserProfile
    from zerver.models.groups import SystemGroups
    from zerver.models.recipients import get_or_create_direct_message_group

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = UserProfile.objects.get(realm=member.realm, delivery_email="polonius@zulip.com")
    admin = UserProfile.objects.get(realm=member.realm, delivery_email="iago@zulip.com")
    hidden = UserProfile.objects.get(realm=member.realm, delivery_email="othello@zulip.com")
    target = UserProfile.objects.get(realm=member.realm, delivery_email="prospero@zulip.com")
    inactive = UserProfile.objects.get(realm=member.realm, delivery_email="cordelia@zulip.com")
    bot = UserProfile.objects.get(realm=member.realm, delivery_email="default-bot@zulip.com")
    foreign_user = UserProfile.objects.filter(
        realm__string_id="zephyr", role=UserProfile.ROLE_MEMBER, is_active=True, is_bot=False
    ).first()
    assert foreign_user is not None and guest.is_guest and admin.is_realm_admin and bot.is_bot
    foreign, _ = create_stream_if_needed(foreign_user.realm, "Pilot foreign subscription")
    members = NamedUserGroup.objects.get(
        realm_for_sharding=member.realm, is_system_group=True, name=SystemGroups.MEMBERS
    )
    nobody = NamedUserGroup.objects.get(
        realm_for_sharding=member.realm, is_system_group=True, name=SystemGroups.NOBODY
    )
    member.realm.can_access_all_users_group = members
    member.realm.save(update_fields=["can_access_all_users_group"])
    Subscription.objects.filter(user_profile=guest, active=True).update(active=False)
    get_or_create_direct_message_group([guest.id, target.id])
    UserProfile.objects.filter(id=inactive.id).update(is_active=False)

    public, _ = create_stream_if_needed(member.realm, "Pilot subscription status")
    web, _ = create_stream_if_needed(member.realm, "Pilot web subscriptions", is_web_public=True)
    archived, _ = create_stream_if_needed(member.realm, "Pilot archived subscriptions")
    Stream.objects.filter(id=archived.id).update(deactivated=True)
    private, _ = create_stream_if_needed(
        member.realm,
        "Pilot private subscriptions",
        invite_only=True,
        can_administer_channel_group=nobody,
        can_add_subscribers_group=nobody,
        can_subscribe_group=nobody,
    )
    inner = check_add_user_group(
        member.realm, "Pilot channel delegates", [member], acting_user=admin
    )
    outer = check_add_user_group(member.realm, "Pilot channel metadata", [], acting_user=admin)
    outer.direct_subgroups.add(inner)
    metadata_cases = []
    group_settings = (
        "can_administer_channel_group",
        "can_add_subscribers_group",
        "can_subscribe_group",
    )
    for setting in group_settings:
        settings_for_channel = dict.fromkeys(group_settings, nobody)
        settings_for_channel[setting] = outer
        metadata, _ = create_stream_if_needed(
            member.realm, f"Pilot metadata {setting}", invite_only=True, **settings_for_channel
        )
        metadata_cases.append((setting, metadata))

    def subscribe(stream: Stream, user: UserProfile, *, active: bool = True) -> None:
        assert stream.recipient_id is not None
        Subscription.objects.create(
            recipient_id=stream.recipient_id,
            user_profile=user,
            is_user_active=user.is_active,
            active=active,
        )

    for stream, user in (
        (public, member),
        (public, bot),
        (web, target),
        (private, target),
        (private, guest),
        (foreign, foreign_user),
    ):
        subscribe(stream, user)
    for _, metadata in metadata_cases:
        subscribe(metadata, target)
    subscribe(public, target, active=False)
    sessions: dict[int, str] = {}

    def authenticate(mode: str, user: UserProfile) -> dict[str, str]:
        if mode == "basic":
            token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
            return {"Authorization": f"Basic {token}"}
        if user.id not in sessions:
            session = SessionStore()
            session.update(
                {
                    "_auth_user_id": str(user.id),
                    "_auth_user_backend": "zproject.backends.DevAuthBackend",
                    "_auth_user_hash": user.get_session_auth_hash(),
                }
            )
            session.create()
            cleanup.callback(session.delete)
            assert session.session_key is not None
            sessions[user.id] = session.session_key
        return {"Cookie": f"{settings.SESSION_COOKIE_NAME}={sessions[user.id]}"}

    def case(
        mode: str,
        label: str,
        actor: UserProfile,
        user_id: int | str,
        stream_id: int | str,
        subscribed: bool | None = None,
        *,
        error: str | None = None,
        status: int | None = None,
        method: str = "GET",
        suffix: str = "",
        language: str | None = None,
        anonymous: bool = False,
    ) -> SubscriptionCase:
        prefix = "/api/v1" if mode == "basic" else "/json"
        headers = {} if anonymous else authenticate(mode, actor)
        if language:
            headers["Accept-Language"] = language
        path = f"{prefix}/users/{user_id}/subscriptions/{stream_id}{suffix}"
        mapped = actor.realm == member.realm and method != "OPTIONS" and user_id != "me"
        return SubscriptionCase(
            f"{mode} {label}",
            method,
            actor.realm.host,
            headers,
            path,
            status if status is not None else 200 if subscribed is not None else 400,
            subscribed,
            error,
            mapped=mapped,
            also_compare_direct=not mapped and actor.realm != member.realm and method == "GET",
        )

    modes = ("basic", "session")
    reads = [case(mode, "member self", member, member.id, public.id, True) for mode in modes]
    cases = [*reads, *(replace(read, label=f"{read.label} HEAD", method="HEAD") for read in reads)]
    for mode in modes:
        cases.extend(
            (
                case(mode, "inactive subscription", member, target.id, public.id, False),
                case(mode, "no subscription row", member, hidden.id, public.id, False),
                case(mode, "bot subscription", member, bot.id, public.id, True),
                case(mode, "guest may read bot", guest, bot.id, web.id, False),
                case(mode, "guest own subscription", guest, guest.id, private.id, True),
                case(mode, "guest shared DM", guest, target.id, web.id, True),
                case(
                    mode,
                    "guest unsubscribed public",
                    guest,
                    bot.id,
                    public.id,
                    error="Invalid channel ID",
                ),
                case(
                    mode,
                    "private channel not visible",
                    member,
                    target.id,
                    private.id,
                    error="Invalid channel ID",
                ),
                case(mode, "administrator metadata", admin, target.id, private.id, True),
                *(
                    case(mode, f"nested {setting}", member, target.id, metadata.id, True)
                    for setting, metadata in metadata_cases
                ),
                case(
                    mode,
                    "archived channel",
                    admin,
                    target.id,
                    archived.id,
                    error="Invalid channel ID",
                ),
                case(
                    mode,
                    "cross realm target",
                    member,
                    foreign_user.id,
                    public.id,
                    error="No such user",
                ),
                case(
                    mode,
                    "cross realm channel",
                    member,
                    target.id,
                    foreign.id,
                    error="Invalid channel ID",
                ),
                case(
                    mode,
                    "foreign realm remains Django",
                    foreign_user,
                    foreign_user.id,
                    foreign.id,
                    True,
                ),
                case(
                    mode,
                    "target visibility before channel",
                    guest,
                    hidden.id,
                    9999999,
                    error="Insufficient permission",
                ),
                case(
                    mode,
                    "inactive target before channel",
                    guest,
                    inactive.id,
                    9999999,
                    error="User is deactivated",
                ),
                case(
                    mode,
                    "missing target before channel",
                    member,
                    10**22,
                    9999999,
                    error="No such user",
                ),
                case(
                    mode,
                    "large missing channel",
                    member,
                    member.id,
                    10**22,
                    error="Invalid channel ID",
                ),
                case(
                    mode,
                    "inaccessible channel French",
                    member,
                    target.id,
                    private.id,
                    language="fr",
                ),
                case(mode, "inaccessible HEAD", member, target.id, private.id, method="HEAD"),
                case(
                    mode,
                    "extra keys and leading zero",
                    member,
                    f"00{member.id}",
                    f"00{public.id}",
                    True,
                    suffix="?client=website&unused_b=1&unused_a=2",
                ),
                case(
                    mode,
                    "path values take precedence",
                    member,
                    member.id,
                    public.id,
                    True,
                    suffix=f"?user_id={hidden.id}&stream_id={archived.id}",
                ),
                case(
                    mode,
                    "no authorization",
                    member,
                    member.id,
                    public.id,
                    status=401,
                    anonymous=True,
                ),
                case(
                    mode,
                    "Django OPTIONS",
                    member,
                    member.id,
                    public.id,
                    status=204,
                    method="OPTIONS",
                    anonymous=True,
                ),
                case(
                    mode,
                    "own settings path remains Django",
                    member,
                    "me",
                    public.id,
                    status=405,
                    method="HEAD",
                ),
            )
        )
    return cases


def validate(case: SubscriptionCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method in {"HEAD", "OPTIONS"}:
        assert result[1] == b"", case.label
    elif case.subscribed is not None:
        assert json.loads(result[1])["is_subscribed"] is case.subscribed, case.label
    elif case.message is not None:
        assert json.loads(result[1])["msg"] == case.message, case.label


def compare(case: SubscriptionCase, actual: ReadResult, expected: ReadResult) -> None:
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


def startup_guards(stage: Path, reads: list[SubscriptionCase]) -> None:
    db = connection.settings_dict
    assert not db["PASSWORD"]
    dsn = make_dsn(dbname=db["NAME"], host=db["HOST"], user=db["USER"], port=db["PORT"])
    for extra, error in (
        ({}, "Subscription status read pilot requires ZULIP_RUST_DATABASE_URL"),
        (
            {"ZULIP_RUST_DATABASE_URL": dsn, "ZULIP_RUST_EXTERNAL_HOST": settings.EXTERNAL_HOST},
            "Subscription status read pilot requires signed Django sessions",
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
        tempfile.TemporaryDirectory(prefix="subscription-status-", dir=root) as tmp,
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
            map_variable="zulip_rust_subscription_status",
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
