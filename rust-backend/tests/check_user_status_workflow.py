# ruff: noqa: INP001
"""Rehearse the split status writer, reader, and rollback with real private broker events."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import django
from check_draft_workflow import Actor, Events, actor, request
from check_user_status_read import MAP, PILOT, ROUTES
from django.conf import settings
from read_activity_fixture import private_activity_broker
from read_proxy_fixture import (
    PrivateReadProxy,
    ReadResult,
    compare_read_headers,
    private_read_proxy,
    validate_fixture,
    validate_local_root,
    verify_private_database,
    wait_until,
)

from zerver.lib.test_rust_http import rust_http_service

ROOT = Path(__file__).resolve().parents[2]
PERSISTENCE_FLAG = "ZULIP_RUST_USER_STATUS_PERSISTENCE"
PREFIXES = ("/api/v1", "/json")
CREATED = {"status_text": "In focus ☀", "emoji_name": "car"}
EMOJI = {"emoji_name": "car", "emoji_code": "1f697", "reaction_type": "unicode_emoji"}
CLEARED = {"emoji_name": "", "emoji_code": "", "reaction_type": "unicode_emoji"}
UPDATED = {"status_text": "On a break آزمایش", "emoji_name": ""}
DURING_OUTAGE = {"status_text": "Still writing during the outage"}
AFTER_RESTART = {"status_text": "Written by the restored Django writer"}
OK = {"result": "success", "msg": ""}


@contextmanager
def native_settings(directory: Path, native: Path) -> Iterator[Path]:
    # Change only this disposable uWSGI process. Record booleans, not
    # database parameters or credentials, as proof that its native code ran.
    base = os.environ["DJANGO_SETTINGS_MODULE"]
    path = os.environ["PYTHONPATH"]
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", base)
    witness = directory / "native-results"
    module = "pilot_status_native_settings"
    (directory / f"{module}.py").write_text(
        f"from {base} import *\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(native.parent)!r})\n"
        "import _zulip_backend\n"
        f"assert Path(_zulip_backend.__file__).resolve() == Path({str(native)!r})\n"
        f"os.environ[{PERSISTENCE_FLAG!r}] = '1'\n"
        "original = _zulip_backend.update_existing_user_status\n"
        "def witnessed_update(*args):\n"
        "    updated = original(*args)\n"
        f"    with Path({str(witness)!r}).open('a') as stream:\n"
        "        stream.write(str(updated) + '\\n')\n"
        "    return updated\n"
        "_zulip_backend.update_existing_user_status = witnessed_update\n"
    )
    with patch.dict(
        os.environ, {"DJANGO_SETTINGS_MODULE": module, "PYTHONPATH": f"{directory}:{path}"}
    ):
        yield witness


class StatusFlow:
    def __init__(self, owner: Actor, peer: Actor, events: Events, expected_users: set[int]) -> None:
        self.owner = owner
        self.peer = peer
        self.events = events
        self.expected_users = expected_users
        self.count = 0

    def write(
        self,
        proxy: PrivateReadProxy,
        prefix: str,
        form: dict[str, str],
        event: dict[str, str],
    ) -> ReadResult:
        result = request(
            proxy,
            self.owner,
            "POST",
            f"{prefix}/users/me/status",
            proxy.django_address,
            body=urlencode(form),
        )
        assert json.loads(result[1]) == OK
        notice = self.events.next()
        assert notice["event"] == {"type": "user_status", "user_id": self.owner.id, **event}
        users = notice["users"]
        assert isinstance(users, list) and all(type(user) is int for user in users)
        assert self.peer.id not in users
        assert len(users) == len(self.expected_users) and set(users) == self.expected_users
        self.events.assert_empty()
        self.count += 1
        return result

    def snapshots(
        self, proxy: PrivateReadProxy, status: dict[str, str], upstream: str
    ) -> tuple[ReadResult, ...]:
        values: list[ReadResult] = []
        for prefix in PREFIXES:
            path = f"{prefix}/users/{self.owner.id}/status"
            result = request(proxy, self.owner, "GET", path, upstream)
            assert json.loads(result[1]) == {**OK, "status": status}
            head = request(proxy, self.owner, "HEAD", path, upstream)
            assert head[1] == b""
            values.extend((result, head))
        self.events.assert_empty()
        return tuple(values)

    def denied(self, proxy: PrivateReadProxy, upstream: str) -> None:
        for prefix in PREFIXES:
            path = f"{prefix}/users/{self.owner.id}/status"
            result = request(proxy, self.peer, "GET", path, upstream, status=400)
            assert json.loads(result[1])["msg"] == "Insufficient permission"
        self.events.assert_empty()

    def wait_for_routes(self, proxy: PrivateReadProxy, upstream: str) -> None:
        consecutive = 0

        def ready() -> bool:
            nonlocal consecutive
            for prefix, headers in zip(PREFIXES, (self.owner.basic, self.owner.web), strict=True):
                result, selected = proxy.observe(
                    "GET", self.owner.host, headers, f"{prefix}/users/{self.owner.id}/status"
                )
                if (result[0], selected) != (200, upstream):
                    consecutive = 0
                    return False
            # An old nginx worker can answer a new connection during reload.
            consecutive += 1
            return consecutive >= 3

        wait_until(ready)

    def cycle(self, proxy: PrivateReadProxy, reader: str) -> tuple[ReadResult, ...]:
        clear = {"status_text": UPDATED["status_text"], **CLEARED}
        return (
            self.write(proxy, PREFIXES[0], CREATED, {**CREATED, **EMOJI}),
            *self.snapshots(proxy, {**CREATED, **EMOJI}, reader),
            self.write(
                proxy, PREFIXES[1], {**UPDATED, "status_text": "  On a break آزمایش  "}, clear
            ),
            *self.snapshots(proxy, {"status_text": UPDATED["status_text"]}, reader),
            self.write(proxy, PREFIXES[0], {"status_text": ""}, {"status_text": ""}),
            *self.snapshots(proxy, {}, reader),
        )


def compare_results(actual: tuple[ReadResult, ...], expected: tuple[ReadResult, ...]) -> None:
    for result, original in zip(actual, expected, strict=True):
        assert result[0] == original[0]
        # JSON key order differs between server implementations.
        assert (json.loads(result[1]) if result[1] else None) == (
            json.loads(original[1]) if original[1] else None
        )
        compare_read_headers(result, original, "status workflow")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    for tool in ("nginx", "uwsgi", "venv", "openssl", "rabbitmq-server", "epmd", "native"):
        parser.add_argument(f"--{tool}", type=Path, required=True)
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("private loopback only")
    if any(name.startswith("ZULIP_RUST_") and name != "ZULIP_RUST_HTTP_BIN" for name in os.environ):
        raise RuntimeError("use a scrubbed environment with only ZULIP_RUST_HTTP_BIN configured")
    native = args.native.resolve(strict=True)
    assert native.is_relative_to(root) and native.name == "_zulip_backend.so"
    validate_fixture(root)
    django.setup()
    verify_private_database()

    from zerver.lib.users import get_user_ids_who_can_access_user
    from zerver.models import NamedUserGroup, Subscription, UserProfile, UserStatus
    from zerver.models.groups import SystemGroups

    assert settings.TORNADO_PROCESSES == 1, "use one private notification queue"
    owner = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    peer = UserProfile.objects.get(realm=owner.realm, delivery_email="polonius@zulip.com")
    assert peer.is_guest and owner.presence_enabled
    owner.realm.can_access_all_users_group = NamedUserGroup.objects.get(
        realm_for_sharding=owner.realm, is_system_group=True, name=SystemGroups.MEMBERS
    )
    owner.realm.save(update_fields=["can_access_all_users_group"])
    Subscription.objects.filter(user_profile=peer, active=True).update(active=False)
    expected_users = set(get_user_ids_who_can_access_user(owner))
    assert owner.id in expected_users and peer.id not in expected_users
    status_rows = UserStatus.objects.filter(user_profile=owner)
    assert not status_rows.exists()

    with (
        tempfile.TemporaryDirectory(prefix="status-flow-", dir=root) as tmp,
        actor(owner) as owner_actor,
        actor(peer) as peer_actor,
    ):
        directory = Path(tmp)
        with private_activity_broker(directory / "broker", args.rabbitmq_server, args.epmd) as (
            broker,
            channel,
        ):
            flow = StatusFlow(owner_actor, peer_actor, Events(channel, owner_actor), expected_users)

            @contextmanager
            def start_proxy(name: str) -> Iterator[PrivateReadProxy]:
                location = directory / name
                location.mkdir(mode=0o700)
                with private_read_proxy(
                    ROOT,
                    location,
                    args.nginx,
                    args.uwsgi,
                    args.venv,
                    (MAP,),
                    openssl_bin=args.openssl,
                    activity=broker,
                ) as proxy:
                    yield proxy

            with start_proxy("baseline") as proxy:
                flow.wait_for_routes(proxy, proxy.django_address)
                proxy.assert_untrusted_tls_rejected(owner_actor.host, "/api/v1/users/me/status")
                baseline = flow.cycle(proxy, proxy.django_address)
                flow.denied(proxy, proxy.django_address)
                baseline_requests = proxy.requests
            assert status_rows.get().status_text == ""
            status_rows.delete()  # Start the independent synthetic cycle without a row.

            secret = directory / "synthetic-signing-input"
            secret.write_text(settings.SECRET_KEY)
            secret.chmod(0o600)
            with native_settings(directory, native) as witness, start_proxy("mixed") as proxy:
                flow.wait_for_routes(proxy, proxy.django_address)
                with rust_http_service(
                    {
                        PILOT: "1",
                        "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                        "ZULIP_RUST_DJANGO_AUTH_BACKENDS": json.dumps(
                            settings.AUTHENTICATION_BACKENDS
                        ),
                        "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
                        "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
                    }
                ) as rust_port:
                    rust = f"127.0.0.1:{rust_port}"
                    proxy.enable_routes((ROUTES,), rust_port, upstreams_per_route=2)
                    flow.wait_for_routes(proxy, rust)
                    compare_results(flow.cycle(proxy, rust), baseline)
                    flow.denied(proxy, rust)
                for prefix in PREFIXES:
                    failed = request(
                        proxy,
                        owner_actor,
                        "GET",
                        f"{prefix}/users/{owner.id}/status",
                        rust,
                        status=502,
                    )
                    assert json.loads(failed[1]) == json.loads(proxy.error_body())
                # This endpoint is still Django; it can commit while Rust's read route is down.
                flow.write(proxy, PREFIXES[1], DURING_OUTAGE, DURING_OUTAGE)
                written = status_rows.get()
                assert written.status_text == DURING_OUTAGE["status_text"]
                proxy.disable_route(ROUTES)
                flow.wait_for_routes(proxy, proxy.django_address)
                rollback = flow.snapshots(proxy, DURING_OUTAGE, proxy.django_address)
                assert witness.read_text().splitlines() == ["False", "True", "True", "True"]
                mixed_requests = proxy.requests

            with start_proxy("restored") as proxy:
                flow.wait_for_routes(proxy, proxy.django_address)
                compare_results(
                    flow.snapshots(proxy, DURING_OUTAGE, proxy.django_address), rollback
                )
                flow.write(proxy, PREFIXES[0], AFTER_RESTART, AFTER_RESTART)
                flow.snapshots(proxy, AFTER_RESTART, proxy.django_address)
                restored_requests = proxy.requests
            row = status_rows.get()
            assert row.id == written.id and row.status_text == AFTER_RESTART["status_text"]
            assert witness.read_text().splitlines() == ["False", "True", "True", "True"]
            assert flow.count == 8
            print(
                f"Private split status workflow passed: {flow.count} committed notices, "
                f"{baseline_requests + mixed_requests + restored_requests} TLS requests, "
                "three warm native updates, and independent read/writer rollback."
            )


if __name__ == "__main__":
    main()
