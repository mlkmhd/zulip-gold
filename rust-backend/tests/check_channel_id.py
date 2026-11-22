# ruff: noqa: INP001
"""Compare the default-off channel ID read with Django in a private proxy."""

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
from urllib.parse import urlencode

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
PILOT = "ZULIP_RUST_CHANNEL_ID_READ_PILOT"
PUBLIC = "Pilot lookup Café"
MAP = "channel-id-map.conf"
ROUTES = "channel-id-routes.conf"


@dataclass(frozen=True)
class ChannelCase(ReadCase):
    expected_id: int | None


def make_cases(cleanup: ExitStack) -> list[ChannelCase]:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.lib.streams import create_stream_if_needed
    from zerver.models import NamedUserGroup, Stream, Subscription, UserProfile
    from zerver.models.groups import SystemGroups

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = UserProfile.objects.get(realm=member.realm, delivery_email="polonius@zulip.com")
    admin = UserProfile.objects.get(realm=member.realm, delivery_email="iago@zulip.com")
    bot = UserProfile.objects.get(realm=member.realm, delivery_email="default-bot@zulip.com")
    outside_group = NamedUserGroup.objects.get(realm__string_id="zephyr", name="Detail outside")
    other = outside_group.direct_members.get()
    group = NamedUserGroup.objects.get(realm=member.realm, name="Detail root")
    nobody = NamedUserGroup.objects.get(
        realm=member.realm, name=SystemGroups.NOBODY, is_system_group=True
    )
    assert guest.is_guest and admin.is_realm_admin and bot.is_bot

    public, _ = create_stream_if_needed(member.realm, PUBLIC)
    other_public, _ = create_stream_if_needed(other.realm, PUBLIC)
    other_only, _ = create_stream_if_needed(other.realm, "Pilot other org")
    web, _ = create_stream_if_needed(member.realm, "Pilot web", is_web_public=True)
    archived, _ = create_stream_if_needed(member.realm, "Pilot archived")
    Stream.objects.filter(id=archived.id).update(deactivated=True)
    private, _ = create_stream_if_needed(
        member.realm,
        "Pilot private",
        invite_only=True,
        can_subscribe_group=nobody,
        can_add_subscribers_group=nobody,
    )
    subscribed, _ = create_stream_if_needed(
        member.realm,
        "Pilot subscribed",
        invite_only=True,
        can_subscribe_group=nobody,
        can_add_subscribers_group=nobody,
    )
    nested, _ = create_stream_if_needed(
        member.realm,
        "Pilot nested",
        invite_only=True,
        can_subscribe_group=group,
        can_add_subscribers_group=nobody,
    )
    assert subscribed.recipient_id is not None
    Subscription.objects.create(
        user_profile=guest, recipient_id=subscribed.recipient_id, is_user_active=guest.is_active
    )

    sessions: dict[int, str] = {}

    def auth(mode: str, user: UserProfile) -> dict[str, str]:
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
        name: str | None,
        stream: Stream | None,
        *,
        status: int | None = None,
        method: str = "GET",
        extra: list[tuple[str, str]] | None = None,
        language: str | None = None,
        anonymous: bool = False,
    ) -> ChannelCase:
        prefix = "/api/v1" if mode == "basic" else "/json"
        headers = {} if anonymous else auth(mode, actor)
        if language is not None:
            headers["Accept-Language"] = language
        query = ([("stream", name)] if name is not None else []) + (extra or [])
        path = f"{prefix}/get_stream_id"
        if query:
            path += "?" + urlencode(query)
        mapped = actor.realm.host == member.realm.host and method != "OPTIONS"
        return ChannelCase(
            f"{mode} {label}",
            method,
            actor.realm.host,
            headers,
            path,
            status if status is not None else 200 if stream is not None else 400,
            stream.id if stream is not None else None,
            mapped=mapped,
            also_compare_direct=not mapped and method == "GET",
        )

    modes = ("basic", "session")
    reads = [case(mode, "public", member, public.name, public) for mode in modes]
    cases = [*reads, *(replace(read, label=f"{read.label} HEAD", method="HEAD") for read in reads)]
    for mode in modes:
        cases.extend(
            (
                case(
                    mode,
                    "folded and trimmed",
                    member,
                    f"\u2003{public.name.swapcase()}\u2003",
                    public,
                ),
                case(
                    mode,
                    "last duplicate with ignored keys",
                    member,
                    "not a channel",
                    public,
                    extra=[
                        ("unused_z", "1"),
                        ("stream", public.name),
                        ("client", "website"),
                        ("unused_a", "2"),
                    ],
                ),
                case(mode, "guest public denied", guest, public.name, None),
                case(mode, "guest web public", guest, web.name, web),
                case(mode, "guest subscribed", guest, subscribed.name, subscribed),
                case(mode, "private denied", member, private.name, None),
                case(mode, "admin without content denied", admin, private.name, None),
                case(mode, "nested permission access", member, nested.name, nested),
                case(mode, "archived denied", member, archived.name, None),
                case(mode, "archived HEAD", member, archived.name, None, method="HEAD"),
                case(mode, "different realm denied", member, other_only.name, None),
                case(mode, "same name in another realm", other, other_public.name, other_public),
                case(mode, "missing channel", member, "unknown channel", None),
                case(
                    mode, "missing channel French", member, "unknown channel", None, language="fr"
                ),
                case(mode, "missing argument", member, None, None),
                case(mode, "blank argument", member, "", None),
                case(mode, "anonymous", member, public.name, None, status=401, anonymous=True),
                case(
                    mode,
                    "Django OPTIONS",
                    member,
                    public.name,
                    None,
                    status=204,
                    method="OPTIONS",
                    anonymous=True,
                ),
            )
        )
    cases.append(case("basic", "bot actor", bot, public.name, public))
    return cases


def validate(case: ChannelCase, result: ReadResult) -> None:
    assert result[0] == case.status, (case.label, result[0], case.status)
    if case.method in {"HEAD", "OPTIONS"}:
        assert result[1] == b"", case.label
    elif case.expected_id is not None:
        assert json.loads(result[1])["stream_id"] == case.expected_id, case.label


def compare(case: ChannelCase, actual: ReadResult, expected: ReadResult) -> None:
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


def startup_guards(stage: Path, reads: list[ChannelCase]) -> None:
    db = connection.settings_dict
    assert not db["PASSWORD"]
    dsn = make_dsn(dbname=db["NAME"], host=db["HOST"], user=db["USER"], port=db["PORT"])
    for extra, error in (
        ({}, "Channel ID read pilot requires ZULIP_RUST_DATABASE_URL"),
        (
            {"ZULIP_RUST_DATABASE_URL": dsn, "ZULIP_RUST_EXTERNAL_HOST": settings.EXTERNAL_HOST},
            "Channel ID read pilot requires signed Django sessions",
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
    with tempfile.TemporaryDirectory(prefix="channel-id-", dir=root) as tmp, ExitStack() as cleanup:
        stage = Path(tmp)
        cases = make_cases(cleanup)
        startup_guards(stage, cases[:2])
        verify_method_map(
            args.nginx,
            stage / "methods",
            tuple(cases[:2]),
            map_filename=MAP,
            map_variable="zulip_rust_channel_id",
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
