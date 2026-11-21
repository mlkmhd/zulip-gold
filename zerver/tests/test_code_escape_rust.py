from importlib import import_module
from types import SimpleNamespace
from typing import SupportsIndex
from unittest import SkipTest
from unittest.mock import patch

from django.test import SimpleTestCase
from markdown import Markdown
from markdown.extensions.codehilite import CodeHiliteExtension
from typing_extensions import override

from zerver.lib.markdown import fenced_code


class CodeEscapeRustTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "escape_fenced_code"):
            raise SkipTest("Rust extension does not support fenced code escaping")

    def test_escapes_html_and_quotes_exactly_once(self) -> None:
        processor = fenced_code.FencedBlockPreprocessor(Markdown())
        with patch.object(fenced_code, "_rust_code_highlight", self.extension):
            self.assertEqual(
                processor._escape('<script a="x">\'&amp;</script>'),
                "&lt;script a=&quot;x&quot;&gt;'&amp;amp;&lt;/script&gt;",
            )
            self.assertEqual(processor._escape("فارسی 😀\r\n\0'"), "فارسی 😀\r\n\0'")
            self.assertEqual(processor._escape(""), "")

    def test_surrogates_large_inputs_and_old_wheel_fall_back(self) -> None:
        processor = fenced_code.FencedBlockPreprocessor(Markdown())
        for source in ["<&>\"'", '\ud800<&"', "x" * (8 * 1024 * 1024) + '<&"']:
            expected = (
                source.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )
            for native in [SimpleNamespace(), self.extension]:
                with patch.object(fenced_code, "_rust_code_highlight", native):
                    self.assertEqual(processor._escape(source), expected)
        self.assertIsNone(self.extension.escape_fenced_code("\ud800"))
        self.assertIsNone(self.extension.escape_fenced_code("x" * (8 * 1024 * 1024 + 1)))

    def test_string_subclass_replace_is_preserved(self) -> None:
        class Source(str):
            __slots__ = ()

            @override
            def replace(self, old: str, new: str, count: SupportsIndex = -1) -> str:
                return "<custom>"

        processor = fenced_code.FencedBlockPreprocessor(Markdown())
        with patch.object(fenced_code, "_rust_code_highlight", self.extension):
            self.assertEqual(processor._escape(Source("original")), "&lt;custom&gt;")

    def test_disabled_highlighting_uses_native_escape(self) -> None:
        processor = fenced_code.FencedBlockPreprocessor(Markdown())
        with (
            patch.object(fenced_code, "_rust_code_highlight", self.extension),
            patch.object(
                self.extension, "escape_fenced_code", wraps=self.extension.escape_fenced_code
            ) as escape,
        ):
            result = processor.format_code(None, '<img src=x onerror="alert(1)">&')
        self.assertEqual(
            result, "<pre><code>&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;\n</code></pre>"
        )
        escape.assert_called_once_with('<img src=x onerror="alert(1)">&')

    def test_highlighter_error_uses_native_escape_without_retry(self) -> None:
        processor = fenced_code.FencedBlockPreprocessor(
            Markdown(extensions=[CodeHiliteExtension()])
        )
        with (
            patch.object(fenced_code, "_rust_code_highlight", self.extension),
            patch.object(
                fenced_code.CodeHilite, "hilite", side_effect=RuntimeError("highlight")
            ) as hilite,
            patch.object(
                self.extension, "escape_fenced_code", wraps=self.extension.escape_fenced_code
            ) as escape,
            self.assertLogs(level="ERROR") as logs,
        ):
            result = processor.format_code(None, '<script>"&')
        self.assertEqual(result, "<pre><code>&lt;script&gt;&quot;&amp;\n</code></pre>")
        self.assertEqual(logs.records[0].getMessage(), "Failed to highlight fenced code block")
        assert logs.records[0].exc_info is not None
        self.assertIs(logs.records[0].exc_info[0], RuntimeError)
        hilite.assert_called_once_with()
        escape.assert_called_once_with('<script>"&')

    def test_native_failure_is_not_retried(self) -> None:
        processor = fenced_code.FencedBlockPreprocessor(Markdown())
        with (
            patch.object(fenced_code, "_rust_code_highlight", self.extension),
            patch.object(
                self.extension, "escape_fenced_code", side_effect=ValueError("escape")
            ) as escape,
            self.assertRaisesRegex(ValueError, "escape"),
        ):
            processor._escape("<x>")
        escape.assert_called_once_with("<x>")
