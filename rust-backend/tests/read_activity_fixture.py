# ruff: noqa: INP001
"""A disposable RabbitMQ broker and activity observations for local read rehearsals."""

from __future__ import annotations

import json
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from secrets import token_hex

import pika
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
from read_proxy_fixture import PrivateActivity, process, wait_until


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


def rust_activity_environment(broker: PrivateActivity) -> dict[str, str]:
    return {
        "ZULIP_RUST_AMQP_URL": (
            f"amqp://{broker.username}:{broker.password}@127.0.0.1:{broker.port}/{broker.vhost}"
        ),
        "ZULIP_RUST_USER_ACTIVITY_SHARDS": str(broker.shards),
    }


class ActivityObserver:
    def __init__(self, channel: BlockingChannel, shards: int) -> None:
        self.channel = channel
        self.queues = [f"user_activity_shard{n + 1}" for n in range(shards)]
        for queue in self.queues:
            result = self.channel.queue_declare(queue=queue, durable=True)
            assert result.method.message_count == result.method.consumer_count == 0
        self.count = 0

    def expect(self, *, user_id: int, client_id: int, query: str, since: int, label: str) -> None:
        queue = self.queues[user_id % len(self.queues)]

        def consume() -> bool:
            method, properties, body = self.channel.basic_get(queue=queue, auto_ack=False)
            if method is None:
                return False
            assert properties is not None and body is not None
            assert not method.redelivered and properties.delivery_mode == 2, label
            event = json.loads(body)
            timestamp = event.pop("time")
            assert isinstance(timestamp, int) and since - 1 <= timestamp <= int(time.time()) + 1
            assert event == {
                "query": query,
                "user_profile_id": user_id,
                "client_id": client_id,
            }, (label, "unexpected activity")
            self.channel.basic_ack(method.delivery_tag)
            return True

        wait_until(consume, timeout=3)
        self.count += 1

    def assert_empty(self) -> None:
        for _ in range(3):
            for queue in self.queues:
                report = self.channel.queue_declare(queue=queue, passive=True)
                assert report.method.message_count == 0, (queue, "unexpected extra activity")
            time.sleep(0.05)
