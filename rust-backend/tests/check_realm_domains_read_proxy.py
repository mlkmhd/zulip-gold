# ruff: noqa: INP001
"""Rehearse the organization-domain read routes and local nginx rollback."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from base64 import b64encode
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import django
from django.conf import settings
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

if TYPE_CHECKING:
    from zerver.models.realms import RealmDomainDict

ROOT = Path(__file__).resolve().parents[2]
MAP = "realm-domains-map.conf"
ROUTE = "realm-domains-routes.conf"
API = "/api/v1/realm/domains"
WEB = "/json/realm/domains"


@dataclass(frozen=True)
class Case(ReadCase):
    domains: list[RealmDomainDict] | None = None
    ignored_parameters: tuple[str, ...] = ()


def decode_domains(body: bytes) -> dict[str, object]:
    document = json.loads(body)
    # Order is not part of the domain-list contract. Sorting preserves
    # multiplicity so it will still catch duplicate/missing entries.
    document["domains"].sort(key=lambda row: row["domain"])
    return document


def validate(case: Case, result: ReadResult) -> None:
    status, body, _, _ = result
    assert status == case.status, (case.label, "status", status, case.status)
    if case.method == "HEAD":
        assert body == b"", (case.label, "HEAD body")
    if case.domains is not None:
        expected = sorted(case.domains, key=lambda row: row["domain"])
        document = decode_domains(body)
        assert document["domains"] == expected, (case.label, "realm domains")
        assert document.get("ignored_parameters_unsupported", []) == list(case.ignored_parameters)


def compare(case: Case, actual: ReadResult, expected: ReadResult) -> None:
    validate(case, actual)
    assert actual[0] == expected[0], (case.label, "status")
    if case.domains is not None:
        assert decode_domains(actual[1]) == decode_domains(expected[1]), (case.label, "JSON")
    else:
        assert actual[1] == expected[1], (case.label, "response body")
    compare_read_headers(actual, expected, case.label)


def pilot(args: argparse.Namespace, directory: Path, cleanup: ExitStack) -> None:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.models import RealmDomain, UserProfile
    from zerver.models.realms import get_realm_domains

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    other_member = UserProfile.objects.get(
        realm__string_id="zulip", delivery_email="othello@zulip.com"
    )
    outside = UserProfile.objects.filter(realm__string_id="zephyr", is_bot=False).first()
    assert outside is not None and member.realm.host == "zulip.testserver"
    assert member.role == UserProfile.ROLE_MEMBER

    for realm, domain, allow_subdomains in (
        (member.realm, "z-pilot-allow.example", True),
        (member.realm, "a-pilot-exact.example", False),
        (outside.realm, "outside-pilot.example", True),
    ):
        row = RealmDomain.objects.create(
            realm=realm, domain=domain, allow_subdomains=allow_subdomains
        )
        cleanup.callback(row.delete)

    home_domains, outside_domains = (
        get_realm_domains(member.realm),
        get_realm_domains(outside.realm),
    )
    assert {row["domain"] for row in home_domains}.isdisjoint(
        row["domain"] for row in outside_domains
    )
    rows = RealmDomain.objects.filter(realm__in=[member.realm, outside.realm]).order_by("id")
    initial_rows = list(rows.values())

    def basic(user: UserProfile) -> dict[str, str]:
        value = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        return {"Authorization": f"Basic {value}"}

    def session(user: UserProfile) -> dict[str, str]:
        store = SessionStore()
        store.update(
            {
                "_auth_user_id": str(user.id),
                "_auth_user_backend": "zproject.backends.DevAuthBackend",
                "_auth_user_hash": user.get_session_auth_hash(),
            }
        )
        store.create()
        cleanup.callback(store.delete)
        assert store.session_key is not None
        return {"Cookie": f"{settings.SESSION_COOKIE_NAME}={store.session_key}"}

    host, outside_host = member.realm.host, outside.realm.host
    api, browser = basic(member), session(member)
    cases = [
        Case("Basic member", "GET", host, api, API, 200, home_domains),
        Case("Session member", "GET", host, browser, WEB, 200, home_domains),
        Case("Basic HEAD", "HEAD", host, api, API, 200),
        Case("Session HEAD", "HEAD", host, browser, WEB, 200),
        Case("Basic query", "GET", host, api, API + "?client=ZulipMobile", 200, home_domains),
        Case("Session query", "GET", host, browser, WEB + "?client=website", 200, home_domains),
        Case("Basic same realm", "GET", host, basic(other_member), API, 200, home_domains),
        Case("Session same realm", "GET", host, session(other_member), WEB, 200, home_domains),
        Case("Basic unauthenticated", "GET", host, {}, API, 401),
        Case("Session unauthenticated", "GET", host, {}, WEB, 401),
        Case(
            "Basic other realm",
            "GET",
            outside_host,
            basic(outside),
            API,
            200,
            outside_domains,
            mapped=False,
            also_compare_direct=True,
        ),
        Case(
            "Session other realm",
            "GET",
            outside_host,
            session(outside),
            WEB,
            200,
            outside_domains,
            mapped=False,
            also_compare_direct=True,
        ),
        Case(
            "Basic wrong realm",
            "GET",
            outside_host,
            api,
            API,
            401,
            mapped=False,
            also_compare_direct=True,
        ),
        Case("Session wrong realm", "GET", outside_host, browser, WEB, 400, mapped=False),
        Case("Basic OPTIONS", "OPTIONS", host, {}, API, 204, mapped=False),
        Case("Session OPTIONS", "OPTIONS", host, {}, WEB, 204, mapped=False),
        Case(
            "Basic detail GET",
            "GET",
            host,
            api,
            API + "/a-pilot-exact.example",
            405,
            mapped=False,
        ),
        Case(
            "Session detail GET",
            "GET",
            host,
            browser,
            WEB + "/a-pilot-exact.example",
            405,
            mapped=False,
        ),
    ]

    with private_read_proxy(
        ROOT, directory, args.nginx, args.uwsgi, args.venv, (MAP,), openssl_bin=args.openssl
    ) as proxy:
        exercise_paired_read_routes(
            proxy, directory, ROUTE, cases, validate=validate, compare=compare
        )
    assert list(rows.values()) == initial_rows, "the read rehearsal changed domain rows"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument(
        "--openssl", type=Path, help="enable private loopback TLS with this OpenSSL"
    )
    parser.add_argument("--uwsgi", type=Path, required=True)
    parser.add_argument("--venv", type=Path, required=True)
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if any(name.startswith("ZULIP_RUST_") and name != "ZULIP_RUST_HTTP_BIN" for name in os.environ):
        raise RuntimeError("use a scrubbed environment with only ZULIP_RUST_HTTP_BIN configured")
    validate_fixture(root)
    django.setup()
    verify_private_database()
    with tempfile.TemporaryDirectory(prefix="net.", dir=root) as directory, ExitStack() as cleanup:
        pilot(args, Path(directory), cleanup)


if __name__ == "__main__":
    main()
