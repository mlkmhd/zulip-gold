# ruff: noqa: INP001

"""Compare actual Zulip rendering on a disposable, private test database."""

import argparse
import json
import os
import socket
import stat
from importlib import import_module
from pathlib import Path
from typing import Any
from unittest.mock import patch

import django
import psycopg2
from django.conf import settings
from django.db import connection, transaction
from psycopg2.extensions import parse_dsn

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "zerver/tests/fixtures/markdown_test_cases.json"
TICKS = chr(96) * 3


def check_database(socket_directory: Path) -> None:
    info = socket_directory.stat()
    db = settings.DATABASES["default"]
    if (
        not settings.TEST_SUITE
        or settings.PRODUCTION
        or settings.KATEX_SERVER
        or settings.USING_RABBITMQ
        or db["NAME"] != "zulip_test"
        or db["HOST"] != str(socket_directory)
        or db["PASSWORD"]
        or set(settings.DATABASES) != {"default"}
        or settings.CACHES["default"]["BACKEND"] != "django.core.cache.backends.locmem.LocMemCache"
        or not socket_directory.is_dir()
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise RuntimeError("read the isolation requirements before using the parity check")


def run_parity() -> None:
    from zerver.actions.create_realm import set_default_for_realm_permission_group_settings
    from zerver.lib.markdown import do_convert, fenced_code
    from zerver.lib.user_groups import create_system_user_groups_for_realm
    from zerver.models import Realm

    backend = import_module("_zulip_backend")
    cases = {
        f"fixture/{case['name']}": case["input"]
        for case in json.loads(FIXTURES.read_text(encoding="utf-8"))["regular_tests"]
        if not case.get("ignore")
        and not case.get("translate_emoticons")
        and ("~~~" in case["input"] or TICKS in case["input"])
    }
    cases.update(
        {
            "default-language": "~~~\nconst answer = 42;\n~~~",
            "unclosed-quote": f"~~~~quote\nQuoted text\n{TICKS}python\nprint('فارسی')",
            "real-katex": "~~~math\nx^2 + y^2\n~~~",
        }
    )
    with transaction.atomic():
        realm = Realm(
            name="Temporary fenced parity",
            string_id="fenced-parity",
            default_code_block_language="javascript",
        )
        # Match normal realm initialization without creating bots or channels.
        for setting_name in Realm.REALM_PERMISSION_GROUP_SETTINGS:
            setattr(realm, setting_name + "_id", -1)
        realm.save()
        create_system_user_groups_for_realm(realm)
        set_default_for_realm_permission_group_settings(realm)
        for realm_mode in (None, realm):
            for label, markdown in cases.items():
                # The same production renderer and hydrated realm are used in
                # both modes. No other native fenced bridge is enabled.
                with patch.object(fenced_code, "_rust_markdown_fences", None):
                    with patch.object(fenced_code, "_rust_markdown_blocks", None):
                        expected = do_convert(markdown, message_realm=realm_mode, no_previews=True)
                    with patch.object(fenced_code, "_rust_markdown_blocks", backend):
                        actual = do_convert(markdown, message_realm=realm_mode, no_previews=True)
                if actual != expected:
                    raise AssertionError(
                        f"planner differed for {label}, realm={realm_mode is not None}"
                    )
                if label == "real-katex" and "katex-display" not in actual.rendered_content:
                    raise AssertionError("the cached KaTeX CLI did not produce actual math output")
                if (
                    label == "default-language"
                    and realm_mode is not None
                    and 'data-code-language="JavaScript"' not in actual.rendered_content
                ):
                    raise AssertionError("the realm default language was not used")
        transaction.set_rollback(True)
    print(f"Matching Zulip rendering results: {len(cases)} inputs × 2 realm modes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-directory", type=Path, required=True)
    args = parser.parse_args()
    socket_directory = args.socket_directory.resolve(strict=True)
    if any(name.startswith("ZULIP_RUST_") and value == "1" for name, value in os.environ.items()):
        parser.error("unset Rust switches; the test changes only the planner")
    original_connect = psycopg2.connect

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("isolated renderer: network access forbidden")

    def isolated_connect(dsn: str | None = None, *args: Any, **kwargs: Any) -> Any:
        parameters = parse_dsn(dsn or "") | kwargs
        if parameters.get("host") != str(socket_directory) or parameters.get("hostaddr"):
            raise RuntimeError("a connection outside the private PostgreSQL socket was requested")
        return original_connect(dsn, *args, **kwargs)

    with (
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(psycopg2, "connect", isolated_connect),
    ):
        check_database(socket_directory)
        django.setup()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT inet_server_addr(), current_database(), current_setting('listen_addresses')"
            )
            if cursor.fetchone() != (None, "zulip_test", ""):
                raise RuntimeError("expected the disposable Unix socket test database")
        run_parity()


if __name__ == "__main__":
    main()
