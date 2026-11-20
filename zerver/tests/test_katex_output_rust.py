import os
import sys
from importlib import import_module
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from typing_extensions import override

from zerver.lib import tex


class KatexOutputRustTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "finish_katex_cli"):
            raise SkipTest("Rust extension does not support KaTeX output normalization")

    def test_newlines_and_unicode_whitespace(self) -> None:
        self.assertEqual(
            self.extension.finish_katex_cli(
                True, "\u001c\u2003α\r\nβ\rγ\r\r\nδ\u0085".encode(), "utf-8"
            ),
            "α\nβ\nγ\n\nδ",
        )
        self.assertEqual(self.extension.finish_katex_cli(True, b" \r\n", "utf-8"), "")
        self.assertEqual(
            self.extension.finish_katex_cli(True, "\u200bx\0\ufeff".encode(), "UTF8"),
            "\u200bx\0\ufeff",
        )
        self.assertIsNone(self.extension.finish_katex_cli(False, b"partial", "utf-8"))

    def test_decode_error_precedes_exit_status(self) -> None:
        for success in [False, True]:
            for output in [b"\xff", b"a\xe2\x82", b"\xed\xa0\x80", b"\xf4\x90\x80\x80"]:
                with self.subTest(success=success, output=output):
                    with self.assertRaises(UnicodeDecodeError) as reference:
                        output.decode("utf-8")
                    with self.assertRaises(UnicodeDecodeError) as actual:
                        self.extension.finish_katex_cli(success, output, "utf-8")
                    self.assertEqual(actual.exception.args, reference.exception.args)

    def test_locale_codecs(self) -> None:
        for encoding in ["latin-1", "utf-16", "utf-32", "utf-8-sig", "utf_8"]:
            with self.subTest(encoding=encoding):
                output = " café\r\nnext\rline\u0085".encode(encoding)
                self.assertEqual(
                    self.extension.finish_katex_cli(True, output, encoding), "café\nnext\nline"
                )
                self.assertIsNone(self.extension.finish_katex_cli(False, output, encoding))
        self.assertEqual(
            self.extension.finish_katex_cli(True, b"\\ud800", "unicode-escape"), "\ud800"
        )

    def test_unknown_codec_error_precedes_exit_status(self) -> None:
        for success in [False, True]:
            with self.subTest(success=success):
                with self.assertRaises(LookupError) as reference:
                    b"x".decode("zulip-invalid-encoding")
                with self.assertRaises(LookupError) as actual:
                    self.extension.finish_katex_cli(success, b"x", "zulip-invalid-encoding")
                self.assertEqual(actual.exception.args, reference.exception.args)

    @override_settings(KATEX_SERVER=False)
    def test_adapter_and_old_wheel_do_not_repeat_process(self) -> None:
        if sys.platform != "linux" or sys.flags.warn_default_encoding:
            self.skipTest("Native KaTeX requires Linux without encoding warnings")
        for native in [self.extension, SimpleNamespace(run_katex_cli=self.extension.run_katex_cli)]:
            with self.subTest(old_wheel=native is not self.extension):
                with (
                    patch.object(tex, "_rust_markdown_tex", native),
                    patch.object(os.path, "isfile", return_value=True),
                    patch.object(
                        native, "run_katex_cli", return_value=(True, b" a\r\nb \n")
                    ) as run,
                ):
                    self.assertEqual(tex.render_tex("x"), "a\nb")
                run.assert_called_once()

    @override_settings(KATEX_SERVER=False)
    def test_adapter_decode_error_does_not_retry(self) -> None:
        if sys.platform != "linux" or sys.flags.warn_default_encoding:
            self.skipTest("Native KaTeX requires Linux without encoding warnings")
        with (
            patch.object(tex, "_rust_markdown_tex", self.extension),
            patch.object(os.path, "isfile", return_value=True),
            patch.object(self.extension, "run_katex_cli", return_value=(False, b"\xff")) as run,
            self.assertRaises(UnicodeDecodeError),
        ):
            tex.render_tex("x")
        run.assert_called_once()
