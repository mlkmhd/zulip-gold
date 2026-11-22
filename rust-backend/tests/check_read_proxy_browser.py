# ruff: noqa: INP001
"""Log into Django with Chrome and rehearse Rust reads with the same browser session."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import select
import signal
import socket
import ssl
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext, suppress
from functools import partial
from pathlib import Path
from secrets import token_urlsafe

import django
from django.conf import settings
from read_activity_fixture import (
    ActivityObserver,
    private_activity_broker,
    rust_activity_environment,
)
from read_proxy_fixture import (
    private_read_proxy,
    validate_fixture,
    validate_local_root,
    verify_private_database,
    wait_until,
)
from read_quota_fixture import QuotaCounter, QuotaSample, private_redis, rust_quota_environment

from zerver.lib.test_rust_http import rust_http_service

ROOT = Path(__file__).resolve().parents[2]
HOST = "zulip.testserver:8880"
ORIGIN = f"https://{HOST}"
ACTIVITY_QUERIES = {
    "/json/saved_snippets": "get_saved_snippets",
    "/json/scheduled_messages": "fetch_scheduled_messages",
    "/json/realm/domains": "list_realm_domains",
}
READS = tuple(ACTIVITY_QUERIES)
MAPS = ("saved-snippets-web-map.conf", "scheduled-messages-map.conf", "realm-domains-map.conf")
ROUTES = (
    (1, ("saved-snippets-web-routes.conf",)),
    (2, ("scheduled-messages-routes.conf", "realm-domains-routes.conf")),
)


class SECItem(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("data", ctypes.c_void_p), ("length", ctypes.c_uint)]


class CertTrust(ctypes.Structure):
    _fields_ = [("ssl", ctypes.c_uint), ("email", ctypes.c_uint), ("object", ctypes.c_uint)]


def install_anchor(db: Path, certificate_der: bytes) -> None:
    db.mkdir(parents=True, mode=0o700)
    n = ctypes.CDLL("libnss3.so")

    def fn(name: str, result: type | None, *args: type) -> ctypes._CFuncPtr:
        f = getattr(n, name)
        f.restype = result
        f.argtypes = args
        return f

    ptr, cstr, status = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int
    assert fn("NSS_InitReadWrite", status, cstr)(f"sql:{db}".encode()) == 0
    slot = fn("PK11_GetInternalKeySlot", ptr)()
    assert slot
    certificate = None
    try:
        if fn("PK11_NeedUserInit", status, ptr)(slot):
            assert fn("PK11_InitPin", status, ptr, cstr, cstr)(slot, None, b"") == 0
        raw = ctypes.create_string_buffer(certificate_der)
        item = SECItem(0, ctypes.cast(raw, ptr), len(certificate_der))
        certdb = fn("CERT_GetDefaultCertDB", ptr)()
        certificate = fn(
            "CERT_NewTempCertificate", ptr, ptr, ctypes.POINTER(SECItem), cstr, status, status
        )(certdb, ctypes.byref(item), None, 0, 1)
        assert certificate
        assert (
            fn("PK11_ImportCert", status, ptr, ptr, ctypes.c_ulong, cstr, status)(
                slot, certificate, 0, b"Zulip private pilot", 0
            )
            == 0
        )
        trust = CertTrust()
        assert (
            fn("CERT_DecodeTrustString", status, ctypes.POINTER(CertTrust), cstr)(
                ctypes.byref(trust), b"C,,"
            )
            == 0
        )
        assert (
            fn("CERT_ChangeCertTrust", status, ptr, ptr, ctypes.POINTER(CertTrust))(
                certdb, certificate, ctypes.byref(trust)
            )
            == 0
        )
    finally:
        if certificate:
            fn("CERT_DestroyCertificate", None, ptr)(certificate)
        fn("PK11_FreeSlot", None, ptr)(slot)
        assert fn("NSS_Shutdown", status)() == 0


@contextmanager
def chrome(
    args: argparse.Namespace, directory: Path
) -> Iterator[Callable[[dict[str, object]], dict[str, object]]]:
    home = directory / "browser-home"
    home.mkdir(mode=0o700)
    certificate = ssl.PEM_cert_to_DER_cert((directory / "localhost.crt").read_text())
    # Chrome on Linux uses an NSS store in one of these locations.
    # Both are under this disposable home, not the user's trust store.
    install_anchor(home / ".pki/nssdb", certificate)
    install_anchor(home / ".local/share/pki/nssdb", certificate)
    arguments = [
        str(args.node),
        str(Path(__file__).with_suffix(".cjs")),
        str(args.puppeteer),
        str(args.chrome),
        str(home / "profile"),
        ORIGIN,
    ]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "ctypes.UTF-8"}
    with (
        (directory / "chrome.log").open("w") as errors,
        subprocess.Popen(
            arguments,
            cwd=directory,
            env=env,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            start_new_session=True,
        ) as child,
    ):
        assert child.stdin is not None and child.stdout is not None

        def send(command: dict[str, object]) -> dict[str, object]:
            child.stdin.write(json.dumps(command) + "\n")
            child.stdin.flush()
            ready, _, _ = select.select([child.stdout], [], [], 15)
            if not ready:
                raise RuntimeError("private browser did not respond")
            line = child.stdout.readline()
            if not line:
                raise RuntimeError("private browser exited: " + errors_last(directory))
            return json.loads(line)

        try:
            yield send
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=6)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)


def errors_last(directory: Path) -> str:
    lines = (directory / "chrome.log").read_text(errors="replace").splitlines()
    return lines[-1] if lines else "no browser message"


def pilot(args: argparse.Namespace, directory: Path) -> None:
    from zerver.models import UserProfile
    from zerver.models.clients import get_client

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    assert member.realm.host == "zulip.testserver"
    assert not member.is_bot and member.is_active and member.rate_limits == ""
    assert settings.SESSION_COOKIE_NAME == "__Host-sessionid" and settings.SESSION_COOKIE_SECURE
    assert settings.CSRF_COOKIE_NAME == "__Host-csrftoken" and settings.CSRF_COOKIE_SECURE
    assert "zproject.backends.EmailAuthBackend" in settings.AUTHENTICATION_BACKENDS
    previous_password = member.password
    password = token_urlsafe(24)
    member.set_password(password)
    member.save(update_fields=["password"])
    try:
        run_session(
            args, directory, member.delivery_email, password, member.id, get_client("website").id
        )
    finally:
        member.password = previous_password
        member.save(update_fields=["password"])


def run_session(
    args: argparse.Namespace,
    directory: Path,
    username: str,
    password: str,
    user_id: int,
    client_id: int,
) -> None:
    redis_context = (
        private_redis(directory, args.redis_server)
        if args.redis_server is not None
        else nullcontext()
    )
    broker_context = (
        private_activity_broker(directory / "rabbit", args.rabbitmq_server, args.epmd)
        if args.rabbitmq_server is not None
        else nullcontext()
    )
    with (
        redis_context as redis_fixture,
        broker_context as activity_fixture,
        private_read_proxy(
            ROOT,
            directory,
            args.nginx,
            args.uwsgi,
            args.venv,
            MAPS,
            openssl_bin=args.openssl,
            browser_login=True,
            quota=redis_fixture[1] if redis_fixture is not None else None,
            activity=activity_fixture[0] if activity_fixture is not None else None,
        ) as proxy,
        chrome(args, directory) as browser,
    ):
        counter = QuotaCounter(*redis_fixture, user_id) if redis_fixture is not None else None
        activity = (
            ActivityObserver(activity_fixture[1], activity_fixture[0].shards)
            if activity_fixture is not None
            else None
        )
        service_environment: dict[str, str] = {}
        if redis_fixture is not None and activity_fixture is not None:
            service_environment = {
                **rust_quota_environment(redis_fixture[1]),
                **rust_activity_environment(activity_fixture[0]),
            }

        def record(
            before: QuotaSample | None,
            response: dict[str, object],
            path: str,
            started: int,
        ) -> None:
            quota = response.pop("quota")
            if counter is not None:
                assert before is not None and isinstance(quota, dict)
                headers = {
                    name: quota[name.lower()]
                    for name in (
                        "X-RateLimit-Limit",
                        "X-RateLimit-Remaining",
                        "X-RateLimit-Reset",
                        "Retry-After",
                    )
                }
                counter.record_allowed(before, (200, b"", headers, None), path)
            if activity is not None:
                activity.expect(
                    user_id=user_id,
                    client_id=client_id,
                    query=ACTIVITY_QUERIES[path],
                    since=started,
                    label=path,
                )

        wait_until(lambda: proxy.request("OPTIONS", HOST, {}, READS[0])[0] == 204)
        before = counter.sample() if counter is not None else None
        started = int(time.time())
        login = browser({"action": "login", "username": username, "password": password})
        assert login.pop("loggedIn") is True and login["status"] == 200
        # The real form redirects to one successful saved-snippet read.
        record(before, login, READS[0], started)
        if activity is not None:
            activity.assert_empty()

        def observe(path: str) -> tuple[dict[str, object], str]:
            proxy.marker += 1
            marker = str(proxy.marker)
            start = proxy.access_log.stat().st_size
            before = counter.sample() if counter is not None else None
            started = int(time.time())
            result = browser({"action": "read", "path": path, "marker": marker})
            if result["status"] == 200:
                record(before, result, path, started)
            else:
                assert result.pop("quota") == dict.fromkeys(
                    (
                        "x-ratelimit-limit",
                        "x-ratelimit-remaining",
                        "x-ratelimit-reset",
                        "retry-after",
                    )
                )
                if counter is not None:
                    assert before is not None and counter.sample().arrival == before.arrival
                if activity is not None:
                    activity.assert_empty()
            routes: list[str] = []

            def logged() -> bool:
                with proxy.access_log.open(encoding="ascii") as log:
                    log.seek(start)
                    for line in log:
                        if line.startswith(marker + " ") and line.endswith("\n"):
                            routes.extend(line.split())
                            return True
                return False

            wait_until(logged, timeout=3)
            assert routes[:2] == [marker, "GET"]
            assert int(routes[4]) == result["status"]
            # nginx logs the internally rewritten URI for the standard error page.
            expected_uri = "/api_5xx.json" if result["status"] == 502 else path
            assert routes[2] == expected_uri
            if path == "/json/realm/domains" and result["status"] == 200:
                data = result["body"]
                assert isinstance(data, dict)
                data["domains"].sort(key=lambda row: row["domain"])
            return result, routes[3]

        def current_workers() -> set[str]:
            master_pid = (directory / "nginx.pid").read_text().strip()
            assert master_pid.isdecimal()
            return set(Path(f"/proc/{master_pid}/task/{master_pid}/children").read_text().split())

        def reconfigure(change: Callable[[], None]) -> None:
            previous = current_workers()
            assert len(previous) == 1
            change()

            def settled() -> bool:
                current = current_workers()
                return len(current) == 1 and current.isdisjoint(previous)

            # Observe the private worker generation without adding HTTP traffic.
            try:
                wait_until(settled, timeout=6)
            except RuntimeError as error:
                raise RuntimeError(
                    f"private reload did not settle: old={sorted(previous)}, current={sorted(current_workers())}"
                ) from error

        original = {}
        for path in READS:
            result, peer = observe(path)
            assert result["status"] == 200 and peer == proxy.django_address
            original[path] = result
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
                **service_environment,
            }
        ) as rust_port:
            rust_address = f"127.0.0.1:{rust_port}"
            for count, routes in ROUTES:
                reconfigure(
                    partial(proxy.enable_routes, routes, rust_port, upstreams_per_route=count)
                )
            for path, expected in original.items():
                assert observe(path) == (expected, rust_address), path

        for path in READS:
            result, peer = observe(path)
            assert peer == rust_address and result == {
                "status": 502,
                "body": json.loads(proxy.error_body()),
            }, path
        for _, routes in ROUTES:
            for route in routes:
                reconfigure(partial(proxy.disable_route, route))
        for path, expected in original.items():
            assert observe(path) == (expected, proxy.django_address), path
        if counter is not None and activity is not None:
            counter.verify_total(1 + 3 * len(READS))
            assert activity.count == 1 + 3 * len(READS)
            activity.assert_empty()
            print(
                "Ten successful browser reads each charged once and produced one activity; three outage reads did neither.",
                flush=True,
            )
        print(
            "One Chrome password-form login retained its secure session across nine routed reads and three outage checks.",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in (
        "fixture-root",
        "nginx",
        "openssl",
        "uwsgi",
        "venv",
        "chrome",
        "node",
        "puppeteer",
    ):
        parser.add_argument(f"--{option}", type=Path, required=True)
    for option in ("redis-server", "rabbitmq-server", "epmd"):
        parser.add_argument(f"--{option}", type=Path)
    args = parser.parse_args()
    services = (args.redis_server, args.rabbitmq_server, args.epmd)
    if any(services) and not all(path is not None and path.is_absolute() for path in services):
        parser.error(
            "combined acceptance requires absolute --redis-server, --rabbitmq-server and --epmd paths"
        )
    if not all(
        getattr(args, name).is_absolute()
        for name in ("nginx", "openssl", "uwsgi", "venv", "chrome", "node", "puppeteer")
    ):
        parser.error("supply absolute paths to the cached browser and test tools")
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("the browser requires a separate network namespace with only loopback")
    if len(Path("/proc/net/route").read_text().splitlines()) != 1:
        raise RuntimeError("the browser fixture may not have external routes")
    if any(name.startswith("ZULIP_RUST_") and name != "ZULIP_RUST_HTTP_BIN" for name in os.environ):
        raise RuntimeError("use a scrubbed environment with only ZULIP_RUST_HTTP_BIN configured")
    validate_fixture(root)
    django.setup()
    verify_private_database()
    with tempfile.TemporaryDirectory(prefix="net.", dir=root) as directory:
        pilot(args, Path(directory))


if __name__ == "__main__":
    main()
