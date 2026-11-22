# ruff: noqa: INP001
"""Verify ordinary read activity delivery on a disposable local RabbitMQ broker."""

from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
import time
from base64 import b64encode
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import django
from django.conf import settings
from read_activity_fixture import (
    ActivityObserver,
    private_activity_broker,
    rust_activity_environment,
)
from read_proxy_fixture import (
    ReadCase,
    private_read_proxy,
    validate_fixture,
    validate_local_root,
    verify_private_database,
)
from read_quota_fixture import LIMIT, QuotaCounter, private_redis, rust_quota_environment

from zerver.lib.test_rust_http import rust_http_service

ROOT = Path(__file__).resolve().parents[2]
MAPS = (
    "saved-snippets-map.conf",
    "saved-snippets-web-map.conf",
    "scheduled-messages-map.conf",
    "realm-domains-map.conf",
)
ROUTE_GROUPS = (
    (1, ("saved-snippets-routes.conf", "saved-snippets-web-routes.conf")),
    (2, ("scheduled-messages-routes.conf", "realm-domains-routes.conf")),
)
PATHS = (
    ("/saved_snippets", "get_saved_snippets"),
    ("/scheduled_messages", "fetch_scheduled_messages"),
    ("/realm/domains", "list_realm_domains"),
)


@dataclass(frozen=True)
class ActivityCase(ReadCase):
    user_id: int
    client_id: int
    query: str


def pilot(args: argparse.Namespace, directory: Path, cleanup: ExitStack) -> None:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.models import NamedUserGroup, UserProfile
    from zerver.models.clients import get_client

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    maps, route_groups, paths, group_pilot = MAPS, ROUTE_GROUPS, PATHS, {}
    if args.user_groups:
        maps = ("user-groups-map.conf",)
        route_groups = ((2, ("user-groups-routes.conf",)),)
        paths = (("/user_groups", "get_user_groups"),)
        group_pilot = {"ZULIP_RUST_USER_GROUP_READ_PILOT": "1"}
    elif args.user_group_details:
        group_id = NamedUserGroup.objects.get(realm=member.realm, name="Detail root").id
        maps = ("user-group-details-map.conf",)
        route_groups = ((2, ("user-group-details-routes.conf",)),)
        paths = (
            (f"/user_groups/{group_id}/members", "get_user_group_members"),
            (f"/user_groups/{group_id}/subgroups", "get_subgroups_of_user_group"),
        )
        group_pilot = {"ZULIP_RUST_USER_GROUP_DETAIL_READ_PILOT": "1"}
    elif args.user_group_membership:
        group_id = NamedUserGroup.objects.get(realm=member.realm, name="Detail root").id
        maps = ("user-group-membership-map.conf",)
        route_groups = ((2, ("user-group-membership-routes.conf",)),)
        paths = ((f"/user_groups/{group_id}/members/{member.id}", "get_is_user_group_member"),)
        group_pilot = {"ZULIP_RUST_USER_GROUP_MEMBERSHIP_READ_PILOT": "1"}
    elif args.channel_id:
        maps = ("channel-id-map.conf",)
        route_groups = ((2, ("channel-id-routes.conf",)),)
        query = urlencode({"stream": "Pilot lookup Café"})
        paths = ((f"/get_stream_id?{query}", "json_get_stream_id"),)
        group_pilot = {"ZULIP_RUST_CHANNEL_ID_READ_PILOT": "1"}

    def with_client(path: str, client: str) -> str:
        separator = "&" if "?" in path else "?"
        return f"{path}{separator}client={client}"

    other_members = UserProfile.objects.filter(
        realm=member.realm, role=UserProfile.ROLE_MEMBER, is_bot=False, is_active=True
    ).exclude(id=member.id)
    browser_user = next(user for user in other_members if user.id % 2 != member.id % 2)
    readiness_user = other_members.exclude(id=browser_user.id).first()
    assert readiness_user is not None and member.realm.host == "zulip.testserver"

    def basic(user: UserProfile) -> dict[str, str]:
        token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    session = SessionStore()
    session.update(
        {
            "_auth_user_id": str(browser_user.id),
            "_auth_user_backend": "zproject.backends.DevAuthBackend",
            "_auth_user_hash": browser_user.get_session_auth_hash(),
        }
    )
    session.create()
    cleanup.callback(session.delete)
    assert session.session_key is not None
    cookie = {"Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}"}
    mobile_id, website_id = get_client("ZulipMobile").id, get_client("website").id
    cases = [
        ActivityCase(
            f"{method} {prefix}{path}",
            method,
            member.realm.host,
            credentials,
            prefix + with_client(path, client),
            200,
            user.id,
            client_id,
            query,
        )
        for path, query in paths
        for prefix, user, credentials, client, client_id in (
            ("/api/v1", member, basic(member), "ZulipMobile", mobile_id),
            ("/json", browser_user, cookie, "website", website_id),
        )
        for method in ("GET", "HEAD")
    ]
    readiness = [
        ActivityCase(
            f"readiness {path}",
            "HEAD",
            member.realm.host,
            basic(readiness_user),
            "/api/v1" + with_client(path, "ZulipMobile"),
            200,
            readiness_user.id,
            mobile_id,
            query,
        )
        for path, query in paths
    ]

    measured_users = (member, browser_user)
    assert all(user.rate_limits == "" for user in (*measured_users, readiness_user))
    assert all(
        3 * sum(case.user_id == user.id for case in cases) < LIMIT // 2 for user in measured_users
    )
    redis_service = (
        private_redis(directory, args.redis_server)
        if args.redis_server is not None
        else nullcontext()
    )

    with (
        redis_service as quota_fixture,
        private_activity_broker(directory / "rabbit", args.rabbitmq_server, args.epmd) as (
            broker,
            channel,
        ),
        private_read_proxy(
            ROOT,
            directory,
            args.nginx,
            args.uwsgi,
            args.venv,
            maps,
            openssl_bin=args.openssl,
            activity=broker,
            quota=quota_fixture[1] if quota_fixture is not None else None,
        ) as proxy,
    ):
        counters: dict[int, QuotaCounter] = {}
        redis_environment: dict[str, str] = {}
        if quota_fixture is not None:
            db, quota = quota_fixture
            redis_environment = rust_quota_environment(quota)
            counters = {user.id: QuotaCounter(db, quota, user.id) for user in measured_users}
        baseline: dict[str, object] = {}
        activity = ActivityObserver(channel, broker.shards)

        def read(case: ActivityCase) -> tuple[int, str]:
            counter = counters.get(case.user_id)
            before = counter.sample() if counter is not None else None
            start = int(time.time())
            response, peer = proxy.observe(case.method, case.host, case.headers, case.path)
            if response[0] != case.status:
                return response[0], peer
            if counter is not None:
                assert before is not None
                counter.record_allowed(before, response, case.label)
            if case.method == "HEAD":
                assert response[1] == b"", case.label
            else:
                document = json.loads(response[1])
                if case.query == "list_realm_domains":
                    document["domains"].sort(key=lambda row: row["domain"])
                elif case.query == "get_user_group_members":
                    document["members"].sort()
                elif case.query == "get_subgroups_of_user_group":
                    document["subgroups"].sort()
                assert document == baseline.setdefault(case.path, document), (case.label, "body")
            activity.expect(
                user_id=case.user_id,
                client_id=case.client_id,
                query=case.query,
                since=start,
                label=case.label,
            )
            return response[0], peer

        def wait_for_target(target: str) -> None:
            consecutive = 0
            latest: list[tuple[int, str]] = []
            for _ in range(6):
                try:
                    latest = [read(case) for case in readiness]
                except (ConnectionError, FileNotFoundError):
                    consecutive = 0
                else:
                    if all(status == 200 and peer == target for status, peer in latest):
                        consecutive += 1
                        if consecutive >= 2:
                            return
                    else:
                        consecutive = 0
                time.sleep(0.35)
            raise RuntimeError(f"private route did not become ready: {latest}")

        def check_phase(upstream: str, label: str) -> None:
            for case in cases:
                assert read(case) == (case.status, upstream), (label, case.label)
            activity.assert_empty()
            evidence = "one activity on its expected shard"
            if counters:
                evidence += " and one Redis charge"
            print(f"{label}: {len(cases)} reads, {evidence} each.", flush=True)

        wait_for_target(proxy.django_address)
        proxy.assert_untrusted_tls_rejected(member.realm.host, f"/api/v1{paths[0][0]}")
        check_phase(proxy.django_address, "Django default")
        secret = directory / "synthetic-secret"
        secret.write_text(settings.SECRET_KEY)
        secret.chmod(0o600)
        with rust_http_service(
            {
                "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                "ZULIP_RUST_DJANGO_AUTH_BACKENDS": json.dumps(settings.AUTHENTICATION_BACKENDS),
                "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
                "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
                "ZULIP_RUST_CLIENT_IP_SOURCE": "proxy",
                **rust_activity_environment(broker),
                **redis_environment,
                **group_pilot,
            }
        ) as rust_port:
            rust_address = f"127.0.0.1:{rust_port}"
            for count, routes in route_groups:
                proxy.enable_routes(routes, rust_port, upstreams_per_route=count)
            wait_for_target(rust_address)
            check_phase(rust_address, "Rust opt-in")

        if counters:
            # The read include still points to Rust, but the process has
            # stopped. A normal request should not reach another backend.
            before = {user_id: counter.sample().arrival for user_id, counter in counters.items()}
            for user in measured_users:
                case = next(
                    case for case in cases if case.user_id == user.id and case.method == "GET"
                )
                failed, peer = proxy.observe(case.method, case.host, case.headers, case.path)
                assert peer == rust_address and failed[0] == 502, case.label
                assert json.loads(failed[1]) == json.loads(proxy.error_body())
            after = {user_id: counter.sample().arrival for user_id, counter in counters.items()}
            assert after == before, "Rust-down probes charged quota"
            activity.assert_empty()
            print(
                "2 normal Rust-down reads returned 502 without quota or activity changes.",
                flush=True,
            )

        for _, routes in route_groups:
            for route in routes:
                proxy.disable_route(route)
        wait_for_target(proxy.django_address)
        check_phase(proxy.django_address, "Django restored")
        for user_id, counter in counters.items():
            counter.verify_total(3 * sum(case.user_id == user_id for case in cases))
        label = " and both quota buckets" if counters else ""
        print(
            f"Verified {3 * len(cases)} ordinary reads across both activity shards{label}.",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument(
        "--openssl", type=Path, help="enable private loopback TLS with this OpenSSL"
    )
    parser.add_argument("--rabbitmq-server", type=Path, required=True)
    parser.add_argument("--epmd", type=Path, required=True)
    parser.add_argument("--redis-server", type=Path, help="also verify shared Redis accounting")
    group_modes = parser.add_mutually_exclusive_group()
    group_modes.add_argument(
        "--user-groups", action="store_true", help="rehearse only the user-group list pilot"
    )
    group_modes.add_argument(
        "--user-group-details",
        action="store_true",
        help="rehearse only membership and subgroup reads",
    )
    group_modes.add_argument(
        "--user-group-membership", action="store_true", help="rehearse one user's group membership"
    )
    group_modes.add_argument(
        "--channel-id", action="store_true", help="rehearse channel lookup by name"
    )
    parser.add_argument("--uwsgi", type=Path, required=True)
    parser.add_argument("--venv", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.user_groups or args.user_group_details or args.user_group_membership or args.channel_id
    ) and (args.redis_server is None or args.openssl is None):
        parser.error("selected pilot requires --redis-server and --openssl")
    if args.redis_server is not None and args.openssl is None:
        parser.error("the combined quota and activity gate requires --openssl")
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if (
        args.user_groups or args.user_group_details or args.user_group_membership or args.channel_id
    ) and socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("the selected pilot requires a private loopback-only network namespace")
    if any(name.startswith("ZULIP_RUST_") and name != "ZULIP_RUST_HTTP_BIN" for name in os.environ):
        raise RuntimeError("use a scrubbed environment with only ZULIP_RUST_HTTP_BIN configured")
    validate_fixture(root)
    django.setup()
    verify_private_database()
    with tempfile.TemporaryDirectory(prefix="net.", dir=root) as directory, ExitStack() as cleanup:
        pilot(args, Path(directory), cleanup)


if __name__ == "__main__":
    main()
