import logging
import subprocess
from importlib import import_module
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, call, patch

import requests
from django.test import SimpleTestCase, override_settings
from typing_extensions import override

from zerver.lib import tex


@override_settings(KATEX_SERVER=True, KATEX_SERVER_PORT=9755, SHARED_SECRET="test-secret")
class KatexResponseRustTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "render_katex_response"):
            raise SkipTest("Rust extension does not support KaTeX responses")

    def test_success_retains_request_and_unicode_strip(self) -> None:
        for is_inline in [True, False]:
            with self.subTest(is_inline=is_inline):
                session = Mock()
                session.post.return_value = SimpleNamespace(
                    status_code=200, content="\u2003<span>فارسی</span>\u0085\n".encode()
                )
                with (
                    patch.object(tex, "_rust_katex_response", self.extension),
                    patch.object(tex, "KatexSession", return_value=session) as factory,
                    patch.object(subprocess, "check_output") as cli,
                    patch.object(
                        self.extension,
                        "render_katex_response",
                        wraps=self.extension.render_katex_response,
                    ) as handler,
                ):
                    self.assertEqual(tex.render_tex("α 😀", is_inline), "<span>فارسی</span>")
                factory.assert_called_once_with()
                session.post.assert_called_once_with(
                    "http://localhost:9755/",
                    data={
                        "content": "α 😀",
                        "is_display": "false" if is_inline else "true",
                        "shared_secret": "test-secret",
                    },
                    proxies={"http": ""},
                )
                handler.assert_called_once()
                cli.assert_not_called()

    def test_bad_request_does_not_decode_or_log(self) -> None:
        session = Mock()
        session.post.return_value = SimpleNamespace(status_code=400, content=b"\xff")
        with (
            patch.object(tex, "_rust_katex_response", self.extension),
            patch.object(tex, "KatexSession", return_value=session),
            patch.object(logging, "warning") as warning,
        ):
            self.assertIsNone(tex.render_tex("bad syntax"))
        warning.assert_not_called()

    def test_failure_logs_unstripped_body_once(self) -> None:
        session = Mock()
        session.post.return_value = SimpleNamespace(status_code=503, content="\n فارسی \n".encode())
        with (
            patch.object(tex, "_rust_katex_response", self.extension),
            patch.object(tex, "KatexSession", return_value=session),
            self.assertLogs(level="WARNING") as logs,
        ):
            self.assertIsNone(tex.render_tex("x"))
        self.assertEqual(
            logs.output, ["WARNING:root:KaTeX rendering service failed: (503) \n فارسی \n"]
        )

    def test_invalid_utf8_propagates_without_retry(self) -> None:
        for status in [200, 500]:
            with self.subTest(status=status):
                session = Mock()
                session.post.return_value = SimpleNamespace(status_code=status, content=b"\xff")
                with (
                    patch.object(tex, "_rust_katex_response", self.extension),
                    patch.object(tex, "KatexSession", return_value=session),
                    patch.object(logging, "warning") as warning,
                    self.assertRaises(UnicodeDecodeError) as error,
                ):
                    tex.render_tex("x")
                self.assertEqual(
                    error.exception.args, ("utf-8", b"\xff", 0, 1, "invalid start byte")
                )
                session.post.assert_called_once()
                warning.assert_not_called()

    def test_timeout_and_request_errors_do_not_reach_response_handler(self) -> None:
        for failure, expected in [
            (
                requests.exceptions.ReadTimeout("timeout"),
                "WARNING:root:KaTeX rendering service timed out with 3 byte long input",
            ),
            (
                requests.exceptions.ConnectionError("failed"),
                "WARNING:root:KaTeX rendering service failed: ConnectionError",
            ),
        ]:
            with self.subTest(failure=failure):
                session = Mock()
                session.post.side_effect = failure
                with (
                    patch.object(tex, "_rust_katex_response", self.extension),
                    patch.object(tex, "KatexSession", return_value=session),
                    patch.object(self.extension, "render_katex_response") as handler,
                    self.assertLogs(level="WARNING") as logs,
                ):
                    self.assertIsNone(tex.render_tex("α😀x"))
                self.assertEqual(logs.output, [expected])
                handler.assert_not_called()
                session.post.assert_called_once()

    def test_disabled_and_old_wheel_fallback(self) -> None:
        for native in [None, SimpleNamespace()]:
            with self.subTest(native=native):
                session = Mock()
                session.post.return_value = SimpleNamespace(
                    status_code=200, content=b" fallback \n"
                )
                with (
                    patch.object(tex, "_rust_katex_response", native),
                    patch.object(tex, "KatexSession", return_value=session),
                ):
                    self.assertEqual(tex.render_tex("x"), "fallback")
                session.post.assert_called_once()

    def test_status_is_read_at_original_decision_points(self) -> None:
        class Response:
            def __init__(self, values: list[int]) -> None:
                self.status = Mock(side_effect=values)
                self.content = b" unstripped \n"

            @property
            def status_code(self) -> int:
                return self.status()

        for values in [[201, 400], [201, 500, 503]]:
            with self.subTest(values=values):
                response = Response(values)
                session = Mock()
                session.post.return_value = response
                with (
                    patch.object(tex, "_rust_katex_response", self.extension),
                    patch.object(tex, "KatexSession", return_value=session),
                    patch.object(logging, "warning") as warning,
                ):
                    self.assertIsNone(tex.render_tex("x"))
                self.assertEqual(response.status.call_args_list, [call()] * len(values))
                if len(values) == 2:
                    warning.assert_not_called()
                else:
                    warning.assert_called_once_with(
                        "KaTeX rendering service failed: (%s) %s", 503, " unstripped \n"
                    )

    def test_logger_failure_propagates_without_retry(self) -> None:
        session = Mock()
        session.post.return_value = SimpleNamespace(status_code=500, content=b"failure")
        failure = RuntimeError("logging failed")
        with (
            patch.object(tex, "_rust_katex_response", self.extension),
            patch.object(tex, "KatexSession", return_value=session),
            patch.object(logging, "warning", side_effect=failure) as warning,
            self.assertRaises(RuntimeError) as error,
        ):
            tex.render_tex("x")
        self.assertIs(error.exception, failure)
        warning.assert_called_once_with("KaTeX rendering service failed: (%s) %s", 500, "failure")
        session.post.assert_called_once()

    @override_settings(KATEX_SERVER=False, DEPLOY_ROOT="/missing-katex")
    def test_local_cli_path_does_not_reach_response_handler(self) -> None:
        with (
            patch.object(tex, "_rust_katex_response", self.extension),
            patch.object(tex, "KatexSession") as session,
            patch.object(self.extension, "render_katex_response") as handler,
            self.assertLogs(level="ERROR") as logs,
        ):
            self.assertIsNone(tex.render_tex("x"))
        self.assertEqual(logs.output, ["ERROR:root:Cannot find KaTeX for latex rendering!"])
        session.assert_not_called()
        handler.assert_not_called()
