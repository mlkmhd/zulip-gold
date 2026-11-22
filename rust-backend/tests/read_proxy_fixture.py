# ruff: noqa: INP001
"""Private nginx/uWSGI infrastructure for read-only parity checks."""

import os
import socket
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from django.conf import settings
from django.db import connection

from zerver.lib.test_rust_http import request_test_http

ReadResult: TypeAlias = tuple[int, bytes, dict[str, str | None], str | None]


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
    arguments: list[str], log: Path, *, env: dict[str, str] | None = None
) -> Iterator[subprocess.Popen[bytes]]:
    with (
        log.open("wb") as output,
        subprocess.Popen(arguments, stdout=output, stderr=output, env=env) as child,
    ):
        try:
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
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
    requests: int = 0
    marker: int = 0

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
        result = request_test_http(
            self.port,
            method,
            host,
            {**headers, "X-Pilot-Case": marker},
            path,
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

    def enable_routes(self, route_names: tuple[str, ...], rust_port: int) -> None:
        for name in route_names:
            assert Path(name).name == name
            content = local_includes((self.stage / name).read_text(), self.include)
            assert content.count("127.0.0.1:9997") == 1
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


@contextmanager
def private_read_proxy(
    repo: Path,
    directory: Path,
    nginx_bin: Path,
    uwsgi_bin: Path,
    venv: Path,
    map_files: tuple[str, ...],
) -> Iterator[PrivateReadProxy]:
    if not all(path.is_absolute() for path in (nginx_bin, uwsgi_bin, venv)):
        raise RuntimeError("supply absolute paths to the cached executables")
    assert all(Path(name).name == name for name in map_files)
    conf = directory / "site.conf"
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
        listen 127.0.0.1:{port};
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
    wsgi.write_text(
        "from django.core.wsgi import get_wsgi_application\napplication = get_wsgi_application()\n"
    )
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
                port, nginx, f"unix:{socket_path}", access_log, include, route_dir, stage
            )
