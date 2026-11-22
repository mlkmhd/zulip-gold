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
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from secrets import token_urlsafe

import django
from django.conf import settings
from read_proxy_fixture import (
    private_read_proxy,
    validate_fixture,
    validate_local_root,
    verify_private_database,
    wait_until,
)

from zerver.lib.test_rust_http import rust_http_service

ROOT = Path(__file__).resolve().parents[2]
HOST = "zulip.testserver:8880"
ORIGIN = f"https://{HOST}"
READS = ("/json/saved_snippets", "/json/scheduled_messages", "/json/realm/domains")
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

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    assert member.realm.host == "zulip.testserver"
    assert settings.SESSION_COOKIE_NAME == "__Host-sessionid" and settings.SESSION_COOKIE_SECURE
    assert settings.CSRF_COOKIE_NAME == "__Host-csrftoken" and settings.CSRF_COOKIE_SECURE
    assert "zproject.backends.EmailAuthBackend" in settings.AUTHENTICATION_BACKENDS
    previous_password = member.password
    password = token_urlsafe(24)
    member.set_password(password)
    member.save(update_fields=["password"])
    try:
        run_session(args, directory, member.delivery_email, password)
    finally:
        member.password = previous_password
        member.save(update_fields=["password"])


def run_session(args: argparse.Namespace, directory: Path, username: str, password: str) -> None:
    with (
        private_read_proxy(
            ROOT,
            directory,
            args.nginx,
            args.uwsgi,
            args.venv,
            MAPS,
            openssl_bin=args.openssl,
            browser_login=True,
        ) as proxy,
        chrome(args, directory) as browser,
    ):
        wait_until(lambda: proxy.request("OPTIONS", HOST, {}, READS[0])[0] == 204)
        assert browser({"action": "login", "username": username, "password": password}) == {
            "loggedIn": True,
        }

        def observe(path: str) -> tuple[dict[str, object], str]:
            proxy.marker += 1
            marker = str(proxy.marker)
            start = proxy.access_log.stat().st_size
            result = browser({"action": "read", "path": path, "marker": marker})
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

        def wait_for_route(peer: str) -> None:
            consecutive = 0

            def ready() -> bool:
                nonlocal consecutive
                if all(observe(path)[1] == peer for path in READS):
                    consecutive += 1
                else:
                    consecutive = 0
                return consecutive >= 2

            wait_until(ready, timeout=6)

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
            }
        ) as rust_port:
            rust_address = f"127.0.0.1:{rust_port}"
            for count, routes in ROUTES:
                proxy.enable_routes(routes, rust_port, upstreams_per_route=count)
            wait_for_route(rust_address)
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
                proxy.disable_route(route)
        wait_for_route(proxy.django_address)
        for path, expected in original.items():
            assert observe(path) == (expected, proxy.django_address), path
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
    args = parser.parse_args()
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
