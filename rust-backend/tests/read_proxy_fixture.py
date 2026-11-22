# ruff: noqa: INP001
"""Private nginx/uWSGI infrastructure for read-only parity checks."""

import json
import os
import socket
import ssl
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TypeAlias, TypeVar

from django.conf import settings
from django.db import connection

from zerver.lib.test_rust_http import request_test_http, rust_http_service

ReadResult: TypeAlias = tuple[int, bytes, dict[str, str | None], str | None]


def compare_read_headers(actual: ReadResult, expected: ReadResult, label: str) -> None:
    mismatched = [
        name for name, value in actual[2].items() if name != "Date" and value != expected[2][name]
    ]
    assert not mismatched, (label, "response headers", mismatched)

    def expires_offset(result: ReadResult) -> float | None:
        if result[3] is None:
            return None
        date = result[2]["Date"]
        assert date is not None, (label, "Date missing from a response with Expires")
        return (parsedate_to_datetime(result[3]) - parsedate_to_datetime(date)).total_seconds()

    actual_offset, expected_offset = expires_offset(actual), expires_offset(expected)
    if actual_offset is None or expected_offset is None:
        assert actual_offset == expected_offset, (label, "Expires presence")
    else:
        # Generation and transport can straddle a one-second HTTP date boundary.
        assert abs(actual_offset - expected_offset) <= 1, (label, "Expires relative to Date")


def validate_local_root(root: Path) -> None:
    if not (
        root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        and root != Path(tempfile.gettempdir()).resolve()
        and root.stat().st_uid == os.getuid()
        and stat.S_IMODE(root.stat().st_mode) == 0o700
        and Path.cwd().resolve().is_relative_to(root)
    ):
        raise RuntimeError("an owner-private temporary fixture is required")


def validate_fixture(root: Path) -> None:
    if (
        not settings.TEST_SUITE
        or settings.PRODUCTION
        or settings.USING_RABBITMQ
        or settings.USING_TORNADO
    ):
        raise RuntimeError("only isolated Django test settings are permitted")
    db = connection.settings_dict
    host = Path(db["HOST"])
    port = str(db["PORT"] or "5432")
    if not host.is_absolute() or not port.isascii() or not port.isdecimal():
        raise RuntimeError("an explicit Unix socket and numeric port are required")
    db_socket = host.resolve(strict=True)
    if not (
        db["NAME"] == "zulip_test"
        and not db["PASSWORD"]
        and db_socket.is_relative_to(root)
        and db_socket.is_dir()
        and db_socket.stat().st_uid == os.getuid()
        and stat.S_IMODE(db_socket.stat().st_mode) == 0o700
        and (db_socket / f".s.PGSQL.{port}").is_socket()
    ):
        raise RuntimeError("only the disposable Unix PostgreSQL fixture is permitted")


def verify_private_database() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT inet_server_addr(), current_database(), current_setting('listen_addresses')"
        )
        if cursor.fetchone() != (None, "zulip_test", ""):
            raise RuntimeError("unexpected PostgreSQL fixture")


def wait_until(probe: Callable[[], bool], timeout: float = 8) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if probe():
                return
        except (ConnectionError, FileNotFoundError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError("private test service did not reach its expected state")


@contextmanager
def process(
    arguments: list[str],
    log: Path,
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    stop_timeout: float = 5,
) -> Iterator[subprocess.Popen[bytes]]:
    with (
        log.open("wb") as output,
        subprocess.Popen(arguments, stdout=output, stderr=output, env=env, cwd=cwd) as child,
    ):
        try:
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=stop_timeout)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def reserve_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def local_includes(content: str, include: Path) -> str:
    return content.replace("/etc/nginx/zulip-include/", f"{include}/")


@dataclass
class PrivateReadProxy:
    port: int
    nginx: list[str]
    django_address: str
    access_log: Path
    include: Path
    route_dir: Path
    stage: Path
    tls_context: ssl.SSLContext | None
    requests: int = 0
    marker: int = 0

    def request(
        self,
        method: str,
        host: str,
        headers: dict[str, str],
        path: str,
        *,
        include_date: bool = False,
    ) -> ReadResult:
        return request_test_http(
            self.port,
            method,
            host,
            headers,
            path,
            include_date=include_date,
            tls_context=self.tls_context,
        )

    def assert_untrusted_tls_rejected(self, host: str, path: str) -> None:
        if self.tls_context is None:
            return
        # This separate client has no trusted certificates and sends no credentials.
        untrusted = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:
            request_test_http(self.port, "HEAD", host, {}, path, tls_context=untrusted)
        except ssl.SSLCertVerificationError:
            return
        raise AssertionError("private TLS unexpectedly accepted an untrusted peer")

    def observe(
        self,
        method: str,
        host: str,
        headers: dict[str, str],
        path: str,
    ) -> tuple[ReadResult, str]:
        self.marker += 1
        marker = str(self.marker)
        size = self.access_log.stat().st_size if self.access_log.exists() else 0
        result = self.request(
            method,
            host,
            {**headers, "X-Pilot-Case": marker},
            path,
            include_date=True,
        )
        row: list[str] = []

        def matching_log() -> bool:
            with self.access_log.open("r", encoding="ascii") as log:
                log.seek(size)
                for line in log:
                    if line.startswith(marker + " ") and line.endswith("\n"):
                        row.extend(line.split())
                        return True
            return False

        wait_until(matching_log, timeout=3)
        _, logged_method, _, peer, status = row
        assert (logged_method, int(status)) == (method, result[0]), (method, path)
        self.requests += 1
        return result, peer

    def enable_routes(
        self, route_names: tuple[str, ...], rust_port: int, *, upstreams_per_route: int = 1
    ) -> None:
        for name in route_names:
            assert Path(name).name == name
            content = local_includes((self.stage / name).read_text(), self.include)
            assert content.count("127.0.0.1:9997") == upstreams_per_route
            route = self.route_dir / name
            route.write_text(content.replace("127.0.0.1:9997", f"127.0.0.1:{rust_port}"))
        self.reload()

    def disable_route(self, name: str) -> None:
        assert Path(name).name == name
        (self.route_dir / name).unlink()
        self.reload()

    def reload(self) -> None:
        subprocess.run([*self.nginx, "-t"], check=True, timeout=8)
        subprocess.run([*self.nginx, "-s", "reload"], check=True, timeout=8)

    def error_body(self) -> bytes:
        return (self.include / "api_5xx.json").read_bytes()


def private_tls(
    directory: Path, openssl_bin: Path | None
) -> tuple[str, str, ssl.SSLContext | None]:
    if openssl_bin is None:
        return "", "", None
    if not openssl_bin.is_absolute():
        raise RuntimeError("supply an absolute OpenSSL path")
    certificate, key = directory / "localhost.crt", directory / "localhost.key"
    subprocess.run(
        [
            str(openssl_bin),
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-noenc",
            "-sha256",
            "-days",
            "1",
            "-subj",
            "/CN=Zulip test loopback",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        timeout=10,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    key.chmod(0o600)
    directives = (
        f"ssl_certificate {certificate}; ssl_certificate_key {key}; ssl_protocols TLSv1.2 TLSv1.3;"
    )
    return " ssl", directives, ssl.create_default_context(cafile=str(certificate))


@dataclass(frozen=True)
class PrivateQuota:
    port: int
    window: int
    limit: int


@dataclass(frozen=True)
class PrivateActivity:
    port: int
    password: str = field(repr=False)
    username: str = "readpilot"
    vhost: str = "read-pilot"
    shards: int = 2


@contextmanager
def private_read_proxy(
    repo: Path,
    directory: Path,
    nginx_bin: Path,
    uwsgi_bin: Path,
    venv: Path,
    map_files: tuple[str, ...],
    *,
    openssl_bin: Path | None = None,
    quota: PrivateQuota | None = None,
    activity: PrivateActivity | None = None,
) -> Iterator[PrivateReadProxy]:
    if not all(path.is_absolute() for path in (nginx_bin, uwsgi_bin, venv)):
        raise RuntimeError("supply absolute paths to the cached executables")
    assert all(Path(name).name == name for name in map_files)
    conf = directory / "site.conf"
    ssl_listen, ssl_directives, tls_context = private_tls(directory, openssl_bin)
    stage = repo / "rust-backend/staging/nginx"
    puppet = repo / "puppet/zulip/files/nginx"
    include = directory / "includes"
    include.mkdir()
    route_dir = include / "app.d"
    route_dir.mkdir()
    for name in ("headers", "api_headers", "proxy"):
        content = (puppet / "zulip-include-common" / name).read_text()
        (include / name).write_text(local_includes(content, include))
    (include / "api_5xx.json").write_bytes(
        (puppet / "zulip-include-frontend/api_5xx.json").read_bytes()
    )
    (directory / "uwsgi_params").write_bytes((puppet / "uwsgi_params").read_bytes())
    socket_path = directory / "uwsgi.sock"
    port = reserve_port()
    access_log = directory / "routes.log"
    nginx_log = directory / "nginx.log"
    nginx = [str(nginx_bin), "-p", f"{directory}/", "-c", str(conf), "-e", str(nginx_log)]
    temp_directives = "\n".join(
        f"    {name}_temp_path {directory}/{name}-temp;"
        for name in ("client_body", "proxy", "uwsgi", "fastcgi", "scgi")
    )
    maps = "\n".join(f"    include {stage / name};" for name in map_files)
    conf.write_text(
        f"""
worker_processes 1;
pid {directory}/nginx.pid;
error_log {nginx_log};
events {{ worker_connections 128; }}
http {{
{temp_directives}
    log_format pilot '$http_x_pilot_case $request_method $uri $upstream_addr $status';
    access_log {access_log} pilot;
    upstream django {{ server unix:{socket_path}; }}
{maps}
    server {{
        listen 127.0.0.1:{port}{ssl_listen};
        {ssl_directives}
        server_name .testserver;
        set $trusted_remote_addr $remote_addr;
        set $trusted_x_forwarded_proto $scheme;
        set $x_proxy_misconfiguration '';
        location /api/ {{
            error_page 502 503 504 /api_5xx.json;
            include {include}/api_headers;
            include uwsgi_params;
        }}
        location /json/ {{
            error_page 502 503 504 /api_5xx.json;
            include uwsgi_params;
        }}
        location = /api_5xx.json {{
            internal;
            include {include}/api_headers;
            default_type application/json;
            alias {include}/api_5xx.json;
        }}
        include {route_dir}/*.conf;
    }}
}}
"""
    )
    wsgi = directory / "pilot_wsgi.py"
    quota_settings = ""
    if quota is not None:
        quota_settings = (
            "from django.conf import settings\n"
            "settings.REDIS_HOST = '127.0.0.1'\n"
            f"settings.REDIS_PORT = {quota.port}\n"
            "settings.REDIS_PASSWORD = None\n"
            "settings.RATE_LIMITING = True\n"
            "settings.RATE_LIMITING_RULES = {**settings.RATE_LIMITING_RULES, "
            f"'api_by_user': [({quota.window}, {quota.limit})]}}\n"
        )
    activity_settings = ""
    if activity is not None:
        activity_settings = (
            "from django.conf import settings\n"
            "settings.USING_RABBITMQ = True\n"
            "settings.RABBITMQ_HOST = '127.0.0.1'\n"
            f"settings.RABBITMQ_PORT = {activity.port}\n"
            f"settings.RABBITMQ_USERNAME = {activity.username!r}\n"
            f"settings.RABBITMQ_PASSWORD = {activity.password!r}\n"
            f"settings.RABBITMQ_VHOST = {activity.vhost!r}\n"
            "settings.RABBITMQ_USE_TLS = False\n"
            f"settings.USER_ACTIVITY_SHARDS = {activity.shards}\n"
        )
    wsgi.write_text(
        "from django.core.wsgi import get_wsgi_application\n"
        + quota_settings
        + activity_settings
        + "application = get_wsgi_application()\n"
    )
    wsgi.chmod(0o600)
    allowed_env = (
        "PATH",
        "HOME",
        "DJANGO_SETTINGS_MODULE",
        "ISOLATED_PG_SOCKET",
        "ISOLATED_PG_ROLE",
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
    )
    env = {name: os.environ[name] for name in allowed_env if name in os.environ}
    # A scrubbed process needs UTF-8 to read the existing Redis Lua source.
    env["LC_ALL"] = "C.UTF-8"
    uwsgi = [
        str(uwsgi_bin),
        "--socket",
        str(socket_path),
        "--home",
        str(venv),
        "--chdir",
        str(Path.cwd()),
        "--wsgi-file",
        str(wsgi),
        "--need-app",
        "--die-on-term",
        "--vacuum",
        "--master",
        "--processes",
        "1",
        "--threads",
        "4",
        "--disable-logging",
    ]

    with process(uwsgi, directory / "uwsgi.log", env=env) as django_process:
        wait_until(lambda: django_process.poll() is None and socket_path.is_socket())
        subprocess.run([*nginx, "-t"], check=True, timeout=8)
        with process([*nginx, "-g", "daemon off;"], directory / "nginx-start.log") as front:
            if front.poll() is not None:
                raise RuntimeError("private nginx could not start")
            yield PrivateReadProxy(
                port,
                nginx,
                f"unix:{socket_path}",
                access_log,
                include,
                route_dir,
                stage,
                tls_context,
            )


@dataclass(frozen=True)
class ReadCase:
    label: str
    method: str
    host: str
    headers: dict[str, str]
    path: str
    status: int
    mapped: bool = field(default=True, kw_only=True)
    also_compare_direct: bool = field(default=False, kw_only=True)

    def __post_init__(self) -> None:
        if self.method not in {"GET", "HEAD", "OPTIONS"}:
            raise ValueError("only read-only request methods are allowed")


CaseT = TypeVar("CaseT", bound=ReadCase)


def exercise_paired_read_routes(
    proxy: PrivateReadProxy,
    directory: Path,
    route: str,
    cases: list[CaseT],
    *,
    validate: Callable[[CaseT, ReadResult], None],
    compare: Callable[[CaseT, ReadResult, ReadResult], None],
    measure: Callable[[PrivateReadProxy, CaseT], tuple[float, float]] | None = None,
) -> None:
    # The first four probes are successful, mapped GET and HEAD on the API
    # and web paths respectively. They cover both targets on reload/outage.
    assert [case.method for case in cases[:4]] == ["GET", "GET", "HEAD", "HEAD"]
    assert all(case.mapped and case.status == 200 for case in cases[:4])
    assert all(cases[index].path == cases[index + 2].path for index in range(2))

    def request(case: CaseT) -> tuple[ReadResult, str]:
        return proxy.observe(case.method, case.host, case.headers, case.path)

    def expect(case: CaseT, upstream: str) -> ReadResult:
        result, actual = request(case)
        assert actual == upstream, (case.label, "wrong upstream", actual, upstream)
        validate(case, result)
        return result

    def wait_for_routes(upstream: str) -> None:
        consecutive = 0

        def ready() -> bool:
            nonlocal consecutive
            if all(request(case)[1] == upstream for case in cases[:2]):
                consecutive += 1
            else:
                consecutive = 0
            # Graceful reload can briefly leave old and new workers active.
            return consecutive >= 3

        wait_until(ready)

    wait_until(lambda: request(cases[0])[0][0] == 200)
    proxy.assert_untrusted_tls_rejected(cases[0].host, cases[0].path)
    original = [expect(case, proxy.django_address) for case in cases]
    django_metrics = [measure(proxy, case) for case in cases[:2]] if measure else []
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
        for case, before in zip(cases, original, strict=True):
            if case.mapped or case.also_compare_direct:
                direct_result = request_test_http(
                    rust_port,
                    case.method,
                    case.host,
                    case.headers,
                    case.path,
                    include_date=True,
                )
                compare(case, direct_result, before)
        proxy.enable_routes((route,), rust_port, upstreams_per_route=2)
        wait_for_routes(rust_address)
        for case, before in zip(cases, original, strict=True):
            target = rust_address if case.mapped else proxy.django_address
            compare(case, expect(case, target), before)
        rust_metrics = [measure(proxy, case) for case in cases[:2]] if measure else []

    for case in cases[:4]:
        failed_result, upstream = request(case)
        assert failed_result[0] == 502 and upstream == rust_address, case.label
        if case.method == "HEAD":
            assert failed_result[1] == b"", case.label
        else:
            assert json.loads(failed_result[1]) == json.loads(proxy.error_body()), case.label
    for case in cases:
        if not case.mapped:
            expect(case, proxy.django_address)

    proxy.disable_route(route)
    wait_for_routes(proxy.django_address)
    for case, before in zip(cases, original, strict=True):
        compare(case, expect(case, proxy.django_address), before)
    direct_count = sum(case.mapped or case.also_compare_direct for case in cases)
    unmapped_count = sum(not case.mapped for case in cases)
    print(
        f"{len(cases)} cases in each of three routing states, {direct_count} direct comparisons, "
        f"4 outage and {unmapped_count} unaffected Django checks while Rust is stopped "
        f"({proxy.requests} private proxy requests including readiness)",
        flush=True,
    )
    for case, django_speed, rust_speed in zip(
        cases[: len(django_metrics)], django_metrics, rust_metrics, strict=True
    ):
        print(
            f"{case.label}: Django {django_speed[0]:.1f} req/s p95 {django_speed[1]:.2f} ms; "
            f"Rust {rust_speed[0]:.1f} req/s p95 {rust_speed[1]:.2f} ms",
            flush=True,
        )
