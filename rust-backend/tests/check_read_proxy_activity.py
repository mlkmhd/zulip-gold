# ruff: noqa: INP001
"""Verify ordinary read activity delivery on a disposable local RabbitMQ broker."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
from base64 import b64encode
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex

import django
import pika
from django.conf import settings
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
from read_proxy_fixture import (
    PrivateActivity,
    ReadCase,
    private_read_proxy,
    process,
    validate_fixture,
    validate_local_root,
    verify_private_database,
    wait_until,
)

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


def reserve_ports() -> tuple[int, int, int]:
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(3)]
        for listener in sockets:
            listener.bind(("127.0.0.1", 0))
        epmd_port, distribution_port, amqp_port = (
            listener.getsockname()[1] for listener in sockets
        )
        return epmd_port, distribution_port, amqp_port


def loopback_listener_present(port: int) -> bool:
    rows = subprocess.check_output(
        ["/usr/bin/ss", "-H", "-ltn", f"sport = :{port}"], text=True, timeout=3
    ).splitlines()
    assert all(row.split()[3] in (f"127.0.0.1:{port}", f"[::1]:{port}") for row in rows), (
        "expected only private broker loopback listeners"
    )
    return bool(rows)


@contextmanager
def private_activity_broker(
    directory: Path, server_bin: Path, epmd_bin: Path
) -> Iterator[tuple[PrivateActivity, BlockingChannel]]:
    if not server_bin.is_absolute() or not epmd_bin.is_absolute():
        raise RuntimeError("supply absolute paths to the cached executables")
    epmd_port, distribution_port, amqp_port = reserve_ports()
    directory.mkdir(mode=0o700)
    (directory / "env").touch()
    (directory / "enabled_plugins").write_text("[].\n")
    config = PrivateActivity(amqp_port, token_hex(16))
    config_path = directory / "rabbitmq.conf"
    config_path.write_text(
        f"listeners.tcp.1 = 127.0.0.1:{amqp_port}\n"
        "distribution.listener.interface = 127.0.0.1\n"
        f"distribution.listener.port_range.min = {distribution_port}\n"
        f"distribution.listener.port_range.max = {distribution_port}\n"
        f"default_user = {config.username}\n"
        f"default_pass = {config.password}\n"
        f"default_vhost = {config.vhost}\n"
        "log.console = true\n"
        "log.console.level = warning\n"
        "log.file = false\n"
        "log.syslog = false\n"
    )
    config_path.chmod(0o600)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(directory),
        "TMPDIR": str(directory),
        "LC_ALL": "C.UTF-8",
        "ERL_EPMD_PORT": str(epmd_port),
        "ERL_EPMD_ADDRESS": "127.0.0.1",
        "ERL_CRASH_DUMP": str(directory / "erl_crash.dump"),
        "RABBITMQ_CONFIG_FILE": str(config_path),
        "RABBITMQ_CONF_ENV_FILE": str(directory / "env"),
        "RABBITMQ_ENABLED_PLUGINS_FILE": str(directory / "enabled_plugins"),
        "RABBITMQ_MNESIA_BASE": str(directory / "mnesia"),
        "RABBITMQ_LOG_BASE": str(directory / "logs"),
        "RABBITMQ_PID_FILE": str(directory / "rabbit.pid"),
        "RABBITMQ_PLUGINS_EXPAND_DIR": str(directory / "plugins"),
        "RABBITMQ_FEATURE_FLAGS_FILE": str(directory / "flags"),
        "RABBITMQ_NODENAME": f"readpilot_{token_hex(8)}@localhost",
        "RABBITMQ_DIST_PORT": str(distribution_port),
        "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS": (
            "-start_epmd false -kernel inet_dist_use_interface {127,0,0,1} +S 2:2 +SDcpu 1 +SDio 1"
        ),
    }
    ports = (epmd_port, distribution_port, amqp_port)
    try:
        with process(
            [str(epmd_bin), "-address", "127.0.0.1", "-port", str(epmd_port)],
            directory / "epmd.log",
            env=env,
            cwd=directory,
        ) as epmd:

            def portmapper_ready() -> bool:
                if epmd.poll() is not None:
                    raise RuntimeError("private EPMD failed to start")
                return loopback_listener_present(epmd_port)

            wait_until(portmapper_ready, timeout=3)
            with process(
                [str(server_bin)], directory / "broker.log", env=env, cwd=directory, stop_timeout=12
            ) as server:
                connection: BlockingConnection | None = None

                def broker_ready() -> bool:
                    nonlocal connection
                    if server.poll() is not None:
                        raise RuntimeError("private RabbitMQ failed to start")
                    if not loopback_listener_present(amqp_port):
                        return False
                    try:
                        connection = pika.BlockingConnection(
                            pika.ConnectionParameters(
                                host="127.0.0.1",
                                port=amqp_port,
                                virtual_host=config.vhost,
                                credentials=pika.PlainCredentials(config.username, config.password),
                                connection_attempts=1,
                                socket_timeout=0.5,
                                stack_timeout=0.8,
                                blocked_connection_timeout=2,
                            )
                        )
                    except pika.exceptions.AMQPConnectionError:
                        return False
                    return True

                wait_until(broker_ready, timeout=14)
                assert connection is not None
                with connection:
                    assert all(loopback_listener_present(port) for port in ports)
                    yield config, connection.channel()

    finally:
        assert not any(loopback_listener_present(port) for port in ports), (
            "private broker listeners remain after shutdown"
        )


@dataclass(frozen=True)
class ActivityCase(ReadCase):
    user_id: int
    client_id: int
    query: str


def pilot(args: argparse.Namespace, directory: Path, cleanup: ExitStack) -> None:
    from django.contrib.sessions.backends.db import SessionStore

    from zerver.models import UserProfile
    from zerver.models.clients import get_client

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
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
            f"{prefix}{path}?client={client}",
            200,
            user.id,
            client_id,
            query,
        )
        for path, query in PATHS
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
            f"/api/v1{path}?client=ZulipMobile",
            200,
            readiness_user.id,
            mobile_id,
            query,
        )
        for path, query in PATHS
    ]

    with (
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
            MAPS,
            openssl_bin=args.openssl,
            activity=broker,
        ) as proxy,
    ):
        queues = [f"user_activity_shard{n + 1}" for n in range(broker.shards)]
        for queue in queues:
            result = channel.queue_declare(queue=queue, durable=True)
            assert result.method.message_count == result.method.consumer_count == 0

        def read(case: ActivityCase) -> tuple[int, str]:
            start = int(time.time())
            response, peer = proxy.observe(case.method, case.host, case.headers, case.path)
            if response[0] != case.status:
                return response[0], peer
            if case.method == "HEAD":
                assert response[1] == b"", case.label
            queue = queues[case.user_id % broker.shards]

            def consume() -> bool:
                method, properties, body = channel.basic_get(queue=queue, auto_ack=False)
                if method is None:
                    return False
                assert properties is not None and body is not None
                assert not method.redelivered and properties.delivery_mode == 2, case.label
                event = json.loads(body)
                timestamp = event.pop("time")
                assert isinstance(timestamp, int) and start - 1 <= timestamp <= int(time.time()) + 1
                assert event == {
                    "query": case.query,
                    "user_profile_id": case.user_id,
                    "client_id": case.client_id,
                }, (case.label, "unexpected activity")
                channel.basic_ack(method.delivery_tag)
                return True

            wait_until(consume, timeout=3)
            return response[0], peer

        def assert_both_queues_empty() -> None:
            for _ in range(3):
                for queue in queues:
                    report = channel.queue_declare(queue=queue, passive=True)
                    assert report.method.message_count == 0, (queue, "unexpected extra activity")
                time.sleep(0.05)

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
            assert_both_queues_empty()
            print(
                f"{label}: {len(cases)} reads, exactly one activity on its expected shard each.",
                flush=True,
            )

        wait_for_target(proxy.django_address)
        proxy.assert_untrusted_tls_rejected(member.realm.host, "/api/v1/saved_snippets")
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
                "ZULIP_RUST_AMQP_URL": (
                    f"amqp://{broker.username}:{broker.password}@127.0.0.1:{broker.port}/{broker.vhost}"
                ),
                "ZULIP_RUST_USER_ACTIVITY_SHARDS": str(broker.shards),
            }
        ) as rust_port:
            rust_address = f"127.0.0.1:{rust_port}"
            for count, routes in ROUTE_GROUPS:
                proxy.enable_routes(routes, rust_port, upstreams_per_route=count)
            wait_for_target(rust_address)
            check_phase(rust_address, "Rust opt-in")

        for _, routes in ROUTE_GROUPS:
            for route in routes:
                proxy.disable_route(route)
        wait_for_target(proxy.django_address)
        check_phase(proxy.django_address, "Django restored")
        print(f"Verified {3 * len(cases)} ordinary reads across both activity shards.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument(
        "--openssl", type=Path, help="enable private loopback TLS with this OpenSSL"
    )
    parser.add_argument("--rabbitmq-server", type=Path, required=True)
    parser.add_argument("--epmd", type=Path, required=True)
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
