import logging
import sys
from importlib import import_module
from types import SimpleNamespace
from typing import cast
from unittest import SkipTest
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings
from typing_extensions import override

from zerver.lib import tex
from zerver.lib.partial import partial


@override_settings(KATEX_SERVER=True, KATEX_SERVER_PORT=9755, SHARED_SECRET="test-secret")
class KatexRequestRustTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "request_katex_service"):
            raise SkipTest("Rust extension does not support KaTeX requests")

    def test_request_and_response_capabilities_are_independent(self) -> None:
        for request_enabled, response_enabled in [
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ]:
            for is_inline in [True, False]:
                with self.subTest(
                    request=request_enabled, response=response_enabled, inline=is_inline
                ):
                    native = SimpleNamespace()
                    request = Mock(wraps=self.extension.request_katex_service)
                    response = Mock(wraps=self.extension.render_katex_response)
                    if request_enabled:
                        native.request_katex_service = request
                    if response_enabled:
                        native.render_katex_response = response
                    session = Mock()
                    session.post.return_value = SimpleNamespace(
                        status_code=200, content=b" math \n"
                    )
                    with (
                        patch.object(tex, "_rust_katex_response", native),
                        patch.object(tex, "KatexSession", return_value=session) as factory,
                    ):
                        self.assertEqual(tex.render_tex("α😀\ud800", is_inline), "math")
                    factory.assert_called_once_with()
                    session.post.assert_called_once_with(
                        "http://localhost:9755/",
                        data={
                            "content": "α😀\ud800",
                            "is_display": "false" if is_inline else "true",
                            "shared_secret": "test-secret",
                        },
                        proxies={"http": ""},
                    )
                    self.assertEqual(request.call_count, int(request_enabled))
                    self.assertEqual(response.call_count, int(response_enabled))

    def test_request_failures_preserve_exception_context(self) -> None:
        def warning(
            seen: list[tuple[BaseException | None, tuple[object, ...]]], /, *args: object
        ) -> None:
            seen.append((sys.exception(), args))

        for at_factory in [False, True]:
            for failure in [
                requests.ReadTimeout("timeout"),
                requests.ConnectionError("connection"),
            ]:
                with self.subTest(factory=at_factory, failure=failure):
                    session = Mock()
                    session.post.side_effect = failure
                    factory = (
                        Mock(side_effect=failure) if at_factory else Mock(return_value=session)
                    )
                    seen: list[tuple[BaseException | None, tuple[object, ...]]] = []

                    with (
                        patch.object(tex, "_rust_katex_response", self.extension),
                        patch.object(tex, "KatexSession", factory),
                        patch.object(logging, "warning", side_effect=partial(warning, seen)),
                    ):
                        self.assertIsNone(tex.render_tex("α😀x"))
                    expected = (
                        ("KaTeX rendering service timed out with %d byte long input", 3)
                        if isinstance(failure, requests.Timeout)
                        else ("KaTeX rendering service failed: %s", "ConnectionError")
                    )
                    self.assertEqual(seen, [(failure, expected)])
                    factory.assert_called_once_with()
                    self.assertEqual(session.post.call_count, int(not at_factory))

    def test_logging_failure_retains_request_as_context(self) -> None:
        failure = requests.ReadTimeout("request failed")
        logging_failure = RuntimeError("logger failed")
        session = Mock()
        session.post.side_effect = failure
        with (
            patch.object(tex, "_rust_katex_response", self.extension),
            patch.object(tex, "KatexSession", return_value=session),
            patch.object(logging, "warning", side_effect=logging_failure) as warning,
            self.assertRaises(RuntimeError) as caught,
        ):
            tex.render_tex("α😀")
        self.assertIs(caught.exception, logging_failure)
        self.assertIs(caught.exception.__context__, failure)
        warning.assert_called_once_with(
            "KaTeX rendering service timed out with %d byte long input", 2
        )
        session.post.assert_called_once()

    def test_unexpected_exception_propagates_without_retry(self) -> None:
        for at_factory in [False, True]:
            with self.subTest(factory=at_factory):
                failure = ValueError("request failed")
                session = Mock()
                session.post.side_effect = failure
                factory = Mock(side_effect=failure) if at_factory else Mock(return_value=session)
                with (
                    patch.object(tex, "_rust_katex_response", self.extension),
                    patch.object(tex, "KatexSession", factory),
                    patch.object(logging, "warning") as warning,
                    self.assertRaises(ValueError) as caught,
                ):
                    tex.render_tex("x")
                self.assertIs(caught.exception, failure)
                warning.assert_not_called()
                factory.assert_called_once_with()
                self.assertEqual(session.post.call_count, int(not at_factory))

    def test_live_settings_and_evaluation_order(self) -> None:
        events: list[object] = []

        class Settings:
            KATEX_SERVER = True

            @property
            def KATEX_SERVER_PORT(self) -> object:
                events.append("port")

                class Port:
                    def __format__(self, spec: str) -> str:
                        events.append(("format", spec))
                        return "9756"

                return Port()

            @property
            def SHARED_SECRET(self) -> str:
                events.append("secret")
                return "live-secret"

        class Inline:
            def __bool__(self) -> bool:
                events.append("inline")
                return False

        session = Mock()
        session.post.side_effect = lambda *args, **kwargs: (
            events.append(("post", args, kwargs)) or SimpleNamespace(status_code=200, content=b"ok")
        )
        factory = Mock(side_effect=lambda: events.append("factory") or session)
        with (
            patch.object(tex, "_rust_katex_response", self.extension),
            patch.object(tex, "KatexSession", factory),
            patch.object(tex, "settings", Settings()),
        ):
            self.assertEqual(tex.render_tex("x", cast(bool, Inline())), "ok")
        self.assertEqual(
            events,
            [
                "factory",
                "port",
                ("format", ""),
                "inline",
                "secret",
                (
                    "post",
                    ("http://localhost:9756/",),
                    {
                        "data": {
                            "content": "x",
                            "is_display": "true",
                            "shared_secret": "live-secret",
                        },
                        "proxies": {"http": ""},
                    },
                ),
            ],
        )

    @override_settings(KATEX_SERVER=False, DEPLOY_ROOT="/missing-katex")
    def test_cli_never_requests_the_service(self) -> None:
        with (
            patch.object(tex, "_rust_katex_response", self.extension),
            patch.object(self.extension, "request_katex_service") as request,
            patch.object(tex, "KatexSession") as factory,
            self.assertLogs(level="ERROR") as logs,
        ):
            self.assertIsNone(tex.render_tex("x"))
        self.assertEqual(logs.output, ["ERROR:root:Cannot find KaTeX for latex rendering!"])
        request.assert_not_called()
        factory.assert_not_called()
