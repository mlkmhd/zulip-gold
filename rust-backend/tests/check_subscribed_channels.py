# ruff: noqa: INP001
"""Rehearse read-only Basic and session subscribed channel IDs in a private TLS proxy."""

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
from check_subscription_status import make_cases as seed_subscriptions
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
PILOT = "ZULIP_RUST_SUBSCRIBED_CHANNELS_READ_PILOT"
MAP = "subscribed-channels-map.conf"
ROUTES = "subscribed-channels-routes.conf"


@dataclass(frozen=True)
class ChannelListCase(ReadCase):
    ids: tuple[int, ...] | None
    message: str | None


def make_cases(cleanup: ExitStack) -> list[ChannelListCase]:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.models import Recipient, Stream, Subscription, UserProfile

    # Reuse the prior request matrix's synthetic users, private/public/archived
    # channels, three group grants, and active/inactive subscriptions.
    seed_subscriptions(cleanup)
    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = UserProfile.objects.get(realm=member.realm, delivery_email="polonius@zulip.com")
    admin = UserProfile.objects.get(realm=member.realm, delivery_email="iago@zulip.com")
    target = UserProfile.objects.get(realm=member.realm, delivery_email="prospero@zulip.com")
    empty = UserProfile.objects.get(realm=member.realm, delivery_email="othello@zulip.com")
    inactive = UserProfile.objects.get(realm=member.realm, delivery_email="cordelia@zulip.com")
    bot = UserProfile.objects.get(realm=member.realm, delivery_email="default-bot@zulip.com")
    foreign = UserProfile.objects.filter(
        realm__string_id="zephyr", role=UserProfile.ROLE_MEMBER, is_active=True, is_bot=False
    ).first()
    assert foreign is not None and guest.is_guest and bot.is_bot

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
    scenario_recipient_ids = [
        stream.recipient_id
        for stream in (public, private, web, archived, foreign_channel, *metadata)
    ]
    Subscription.objects.filter(
        user_profile__in=(member, guest, target, empty, bot, foreign),
        recipient__type=Recipient.STREAM,
        active=True,
    ).exclude(recipient_id__in=scenario_recipient_ids).update(active=False)
    for user, stream in ((target, archived), (bot, web)):
        assert stream.recipient_id is not None
        Subscription.objects.create(
            recipient_id=stream.recipient_id, user_profile=user, is_user_active=True
        )
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
        user_id: int | str,
        ids: tuple[int, ...] | None = None,
        *,
        error: str | None = None,
        status: int | None = None,
        method: str = "GET",
        suffix: str = "",
        language: str | None = None,
        anonymous: bool = False,
        host: str | None = None,
    ) -> ChannelListCase:
        prefix = "/api/v1" if mode == "basic" else "/json"
        auth = {} if anonymous else headers(mode, actor)
        if language:
            auth["Accept-Language"] = language
        path = f"{prefix}/users/{user_id}/channels{suffix}"
        mapped = actor.realm == member.realm and method != "OPTIONS" and user_id != "me"
        return ChannelListCase(
            f"{mode} {label}",
            method,
            host or actor.realm.host,
            auth,
            path,
            status if status is not None else 200 if ids is not None else 400,
            ids,
            error,
            mapped=mapped,
            also_compare_direct=not mapped and actor.realm != member.realm and method == "GET",
        )

    modes = ("basic", "session")
    visible = (web.id, archived.id, *(stream.id for stream in metadata))
    reads = [case(mode, "visible channels", member, target.id, visible) for mode in modes]
    cases = [*reads, *(replace(read, label=f"{read.label} HEAD", method="HEAD") for read in reads)]
    for mode in modes:
        cases.extend(
            (
                case(
                    mode,
                    "administrator sees private metadata",
                    admin,
                    target.id,
                    (*visible, private.id),
                ),
                case(
                    mode, "guest sees only shared and web", guest, target.id, (private.id, web.id)
                ),
                case(mode, "guest self", guest, guest.id, (private.id,)),
                case(mode, "guest sees eligible bot channels", guest, bot.id, (web.id,)),
                case(mode, "bot subscription list", member, bot.id, (public.id, web.id)),
                case(mode, "empty subscribed channels", member, empty.id, ()),
                case(
                    mode,
                    "target private to guest",
                    guest,
                    empty.id,
                    error="Insufficient permission",
                ),
                case(mode, "inactive target", guest, inactive.id, error="User is deactivated"),
                case(mode, "foreign target", member, foreign.id, error="No such user"),
                case(mode, "other host remains Django", foreign, foreign.id, (foreign_channel.id,)),
                case(mode, "unknown target", member, 9999999, error="No such user"),
                case(mode, "large unknown target", member, 10**22, error="No such user"),
                case(mode, "inactive target French", member, inactive.id, language="fr"),
                case(mode, "invisible target HEAD", guest, empty.id, method="HEAD"),
                case(
                    mode,
                    "path-only with leading zero",
                    member,
                    f"00{target.id}",
                    visible,
                    suffix=f"?client=website&user_id={guest.id}&other=1",
                ),
                case(
                    mode,
                    "allowlisted port",
                    member,
                    target.id,
                    visible,
                    host="zulip.testserver:8880",
                ),
                case(mode, "no authorization", member, target.id, status=401, anonymous=True),
                case(
                    mode,
                    "Django OPTIONS",
                    member,
                    target.id,
                    status=204,
                    method="OPTIONS",
                    anonymous=True,
                ),
            )
        )
    return cases


def validate(case: ChannelListCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method in {"HEAD", "OPTIONS"}:
        assert result[1] == b"", case.label
    elif case.ids is not None:
        assert sorted(json.loads(result[1])["subscribed_channel_ids"]) == sorted(case.ids), (
            case.label
        )
    elif case.message is not None:
        assert json.loads(result[1])["msg"] == case.message, case.label


def compare(case: ChannelListCase, actual: ReadResult, expected: ReadResult) -> None:
    validate(case, actual)
    if case.method == "GET":
        got, wanted = json.loads(actual[1]), json.loads(expected[1])
        # OpenAPI guarantees the list of IDs but specifies no ordering.
        if "subscribed_channel_ids" in got and "subscribed_channel_ids" in wanted:
            got["subscribed_channel_ids"].sort()
            wanted["subscribed_channel_ids"].sort()
        assert got == wanted, (case.label, got, wanted)
    else:
        assert actual[1] == expected[1], case.label
    compare_read_headers(actual, expected, case.label)


def startup_guards(stage: Path, reads: list[ChannelListCase]) -> None:
    db = connection.settings_dict
    assert not db["PASSWORD"]
    dsn = make_dsn(dbname=db["NAME"], host=db["HOST"], user=db["USER"], port=db["PORT"])
    for extra, error in (
        ({}, "Subscribed channels read pilot requires ZULIP_RUST_DATABASE_URL"),
        (
            {"ZULIP_RUST_DATABASE_URL": dsn, "ZULIP_RUST_EXTERNAL_HOST": settings.EXTERNAL_HOST},
            "Subscribed channels read pilot requires signed Django sessions",
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
        tempfile.TemporaryDirectory(prefix="subscribed-channels-", dir=root) as tmp,
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
            map_variable="zulip_rust_subscribed_channels",
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
