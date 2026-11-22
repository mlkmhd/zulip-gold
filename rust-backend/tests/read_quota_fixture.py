# ruff: noqa: INP001
"""Private Redis and once-per-read accounting for the local proxy rehearsals."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import redis
from read_proxy_fixture import PrivateQuota, ReadResult, process, reserve_port, wait_until

WINDOW = 14_400
LIMIT = 100


@contextmanager
def private_redis(directory: Path, redis_bin: Path) -> Iterator[tuple[redis.Redis, PrivateQuota]]:
    if not redis_bin.is_absolute():
        raise RuntimeError("supply an absolute redis-server path")
    socket_path = directory / "quota.sock"
    config = PrivateQuota(reserve_port(), WINDOW, LIMIT)
    arguments = [
        str(redis_bin),
        "--bind",
        "127.0.0.1",
        "--port",
        str(config.port),
        "--unixsocket",
        str(socket_path),
        "--unixsocketperm",
        "700",
        "--protected-mode",
        "yes",
        "--daemonize",
        "no",
        "--save",
        "",
        "--appendonly",
        "no",
        "--dir",
        str(directory),
        "--loglevel",
        "warning",
    ]
    with process(arguments, directory / "redis.log") as child:
        wait_until(lambda: child.poll() is None and socket_path.is_socket())
        client: redis.Redis = redis.Redis(unix_socket_path=str(socket_path), socket_timeout=2)
        try:
            server = client.info("server")
            assert server["process_id"] == child.pid and server["tcp_port"] == config.port
            assert client.config_get("bind") == {"bind": "127.0.0.1"}
            assert client.config_get("dir") == {"dir": str(directory)}
            assert client.dbsize() == 0, "Redis fixture must be empty"
            yield client, config
        finally:
            client.close()


def rust_quota_environment(quota: PrivateQuota) -> dict[str, str]:
    return {
        "ZULIP_RUST_REDIS_URL": f"redis://127.0.0.1:{quota.port}/0",
        "ZULIP_RUST_RATE_LIMIT_RULES": json.dumps([[quota.window, quota.limit]]),
    }


@dataclass(frozen=True)
class QuotaSample:
    arrival: float | None
    time: float


class QuotaCounter:
    def __init__(self, client: redis.Redis, quota: PrivateQuota, user_id: int) -> None:
        self.client = client
        self.key = f"ratelimit:RateLimitedUser:{user_id}:api_by_user:gcra"
        self.field = f"{quota.window}:{quota.limit}"
        self.limit = quota.limit
        assert quota.window % quota.limit == 0
        self.step = quota.window // quota.limit
        self.count = 0

    def sample(self) -> QuotaSample:
        value = self.client.hget(self.key, self.field)
        seconds, microseconds = self.client.time()
        return QuotaSample(
            None if value is None else float(value), seconds + microseconds / 1_000_000
        )

    def record_allowed(self, before: QuotaSample, reply: ReadResult, label: str) -> None:
        assert reply[0] == 200
        assert (before.arrival is None) == (self.count == 0), (label, "unexpected bucket")
        after = self.sample()
        assert after.arrival is not None
        if before.arrival is None:
            # Django's test clock and Redis are on the same host.
            assert before.time + self.step - 0.05 <= after.arrival <= after.time + self.step + 0.05
        else:
            assert before.arrival > after.time, (label, "fixture unexpectedly refilled")
            assert abs(after.arrival - before.arrival - self.step) < 1e-6, (label, "Redis charge")
        self.count += 1
        headers = reply[2]
        assert headers["X-RateLimit-Limit"] == str(self.limit), (label, "limit")
        assert headers["X-RateLimit-Remaining"] == str(self.limit - self.count), (
            label,
            "remaining",
        )
        reset = headers["X-RateLimit-Reset"]
        assert reset is not None and abs(int(reset) - int(after.arrival)) <= 1, (label, "reset")
        assert headers["Retry-After"] is None

    def verify_total(self, expected: int) -> None:
        assert self.count == expected
        assert set(self.client.hkeys(self.key)) == {self.field.encode()}
