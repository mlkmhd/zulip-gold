import locale
import os
import sys
from importlib import import_module
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from typing_extensions import override

from zerver.lib import tex


@override_settings(KATEX_SERVER=False)
class KatexInputRustTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not all(
            hasattr(self.extension, name)
            for name in ("select_katex_encoding", "encode_katex_input")
        ):
            raise SkipTest("Rust extension does not support KaTeX input encoding")

    def test_utf8_mode_precedes_locale_lookup(self) -> None:
        for mode in [0, 1]:
            with self.subTest(mode=mode):
                getencoding = Mock(return_value="latin-1")
                context = {
                    "sys": SimpleNamespace(flags=SimpleNamespace(utf8_mode=mode)),
                    "locale": SimpleNamespace(getencoding=getencoding),
                }
                self.assertEqual(
                    self.extension.select_katex_encoding(context), "utf-8" if mode else "latin-1"
                )
                self.assertEqual(getencoding.call_count, int(not mode))

    def test_native_utf8_and_python_codecs_preserve_bytes_and_errors(self) -> None:
        for content in ["", "فارسی 😀\0\r\n", "café", "\ud800", "x\udfffy"]:
            for encoding in [
                "utf-8",
                "UTF8",
                "utf_8",
                "latin-1",
                "utf-16",
                "ascii",
                "invalid-codec",
            ]:
                with self.subTest(content=content, encoding=encoding):
                    try:
                        expected = content.encode(encoding)
                    except (UnicodeError, LookupError) as error:
                        with self.assertRaises(type(error)) as caught:
                            self.extension.encode_katex_input(content, encoding)
                        self.assertEqual(caught.exception.args, error.args)
                    else:
                        self.assertEqual(
                            self.extension.encode_katex_input(content, encoding), expected
                        )

    def test_string_subclass_keeps_its_encoder(self) -> None:
        encodings = []

        class Content(str):
            __slots__ = ()

            @override
            def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
                encodings.append((encoding, errors))
                return b"custom-input"

        self.assertEqual(self.extension.encode_katex_input(Content("x"), "utf-8"), b"custom-input")
        self.assertEqual(encodings, [("utf-8", "strict")])

    def test_adapter_capabilities_are_independent(self) -> None:
        if sys.platform != "linux" or sys.flags.warn_default_encoding:
            self.skipTest("Native KaTeX requires Linux without encoding warnings")
        for select_enabled, encode_enabled in [
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ]:
            with self.subTest(select=select_enabled, encode=encode_enabled):
                run = Mock(return_value=(True, b" caf\xe9 \n"))  # codespell:ignore caf
                native = SimpleNamespace(run_katex_cli=run)
                select = Mock(wraps=self.extension.select_katex_encoding)
                encode = Mock(wraps=self.extension.encode_katex_input)
                if select_enabled:
                    native.select_katex_encoding = select
                if encode_enabled:
                    native.encode_katex_input = encode
                with (
                    patch.object(tex, "_rust_markdown_tex", native),
                    patch.object(os.path, "isfile", return_value=True),
                    patch.object(
                        tex,
                        "sys",
                        SimpleNamespace(
                            platform="linux",
                            flags=SimpleNamespace(warn_default_encoding=0, utf8_mode=0),
                        ),
                    ),
                    patch.object(locale, "getencoding", return_value="latin-1") as getencoding,
                ):
                    self.assertEqual(tex.render_tex("café\r\n"), "café")
                run.assert_called_once()
                self.assertEqual(
                    run.call_args.args[1:],
                    (b"caf\xe9\r\n", True),  # codespell:ignore caf
                )
                getencoding.assert_called_once_with()
                self.assertEqual(select.call_count, int(select_enabled))
                self.assertEqual(encode.call_count, int(encode_enabled))

    def test_encoding_failure_does_not_start_process(self) -> None:
        if sys.platform != "linux" or sys.flags.warn_default_encoding:
            self.skipTest("Native KaTeX requires Linux without encoding warnings")
        with (
            patch.object(tex, "_rust_markdown_tex", self.extension),
            patch.object(os.path, "isfile", return_value=True),
            patch.object(self.extension, "run_katex_cli") as run,
            patch.object(self.extension, "select_katex_encoding", return_value="utf-8"),
            self.assertRaises(UnicodeEncodeError) as caught,
        ):
            tex.render_tex("\ud800")
        self.assertEqual(caught.exception.args, ("utf-8", "\ud800", 0, 1, "surrogates not allowed"))
        run.assert_not_called()

    def test_selection_error_does_not_enter_process_fallback(self) -> None:
        if sys.platform != "linux" or sys.flags.warn_default_encoding:
            self.skipTest("Native KaTeX requires Linux without encoding warnings")
        failure = NotImplementedError("locale unavailable")
        check = Mock(return_value="fallback")
        with (
            patch.object(tex, "_rust_markdown_tex", self.extension),
            patch.object(os.path, "isfile", return_value=True),
            patch.object(self.extension, "run_katex_cli") as run,
            patch.object(self.extension, "select_katex_encoding", side_effect=failure),
            patch.object(tex, "_subprocess_check_output", check),
            patch.object(tex.subprocess, "check_output", check),
            self.assertRaises(NotImplementedError) as caught,
        ):
            tex.render_tex("x")
        self.assertIs(caught.exception, failure)
        run.assert_not_called()
        check.assert_not_called()

    def test_encoder_not_implemented_retains_process_fallback(self) -> None:
        if sys.platform != "linux" or sys.flags.warn_default_encoding:
            self.skipTest("Native KaTeX requires Linux without encoding warnings")
        check = Mock(return_value=" fallback \n")
        with (
            patch.object(tex, "_rust_markdown_tex", self.extension),
            patch.object(os.path, "isfile", return_value=True),
            patch.object(self.extension, "run_katex_cli") as run,
            patch.object(self.extension, "encode_katex_input", side_effect=NotImplementedError),
            patch.object(tex, "_subprocess_check_output", check),
            patch.object(tex.subprocess, "check_output", check),
        ):
            self.assertEqual(tex.render_tex("x"), "fallback")
        run.assert_not_called()
        check.assert_called_once()
