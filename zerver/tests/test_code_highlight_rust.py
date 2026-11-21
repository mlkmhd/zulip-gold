from importlib import import_module
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

import lxml.html
from django.test import SimpleTestCase
from markdown import Markdown
from markdown.extensions.codehilite import CodeHiliteExtension
from typing_extensions import override

from zerver.lib.markdown import fenced_code


class CodeHighlightRustTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "prepare_fenced_highlight"):
            raise SkipTest("Rust extension does not support code highlighting")

    def test_real_highlighting_and_escaped_fallback(self) -> None:
        for extensions in [[], [CodeHiliteExtension()]]:
            for language in [None, "", "js", "javascript", "php", "unknown-language", "<custom>"]:
                with self.subTest(extensions=bool(extensions), language=language):
                    results = []
                    for native in [None, self.extension]:
                        processor = fenced_code.FencedBlockPreprocessor(
                            Markdown(extensions=extensions)
                        )
                        with patch.object(fenced_code, "_rust_code_highlight", native):
                            results.append(processor.format_code(language, "<?php\n<x>& فارسی\n"))
                        self.assertTrue(processor.checked_for_codehilite)
                    self.assertEqual(*results)
                    self.assertNotIn("<custom>", results[0])

    def test_configuration_is_cached_and_first_extension_wins(self) -> None:
        first = CodeHiliteExtension(css_class="first")
        second = CodeHiliteExtension(css_class="second")
        md = Markdown()
        md.registeredExtensions = [first, second]
        processor = fenced_code.FencedBlockPreprocessor(md)
        with patch.object(fenced_code, "_rust_code_highlight", self.extension):
            self.assertIn('class="first"', processor.format_code(None, "x"))
            self.assertIs(processor.codehilite_conf, first.config)
            md.registeredExtensions = []
            self.assertIn('class="first"', processor.format_code(None, "y"))
        self.assertTrue(processor.checked_for_codehilite)

    def test_highlighter_failure_keeps_exception_context_and_escapes(self) -> None:
        processor = fenced_code.FencedBlockPreprocessor(
            Markdown(extensions=[CodeHiliteExtension()])
        )
        with (
            patch.object(fenced_code, "_rust_code_highlight", self.extension),
            patch.object(
                fenced_code.CodeHilite, "hilite", side_effect=ValueError("crashed")
            ) as hilite,
            self.assertLogs(level="ERROR") as logs,
        ):
            result = processor.format_code("unknown-language", "<script>&")
        self.assertIn("&lt;script&gt;&amp;", result)
        self.assertIn('data-code-language="unknown-language"', result)
        self.assertEqual(logs.records[0].getMessage(), "Failed to highlight fenced code block")
        assert logs.records[0].exc_info is not None
        self.assertIs(logs.records[0].exc_info[0], ValueError)
        hilite.assert_called_once_with()

    def test_constructor_failure_is_not_caught_or_retried(self) -> None:
        processor = fenced_code.FencedBlockPreprocessor(
            Markdown(extensions=[CodeHiliteExtension()])
        )
        with (
            patch.object(fenced_code, "_rust_code_highlight", self.extension),
            patch.object(
                fenced_code, "CodeHilite", side_effect=ValueError("constructor")
            ) as factory,
            self.assertRaisesRegex(ValueError, "constructor"),
        ):
            processor.format_code("python", "x")
        factory.assert_called_once()
        self.assertTrue(processor.checked_for_codehilite)

    def test_none_constructor_result_still_logs_highlight_failure(self) -> None:
        processor = fenced_code.FencedBlockPreprocessor(
            Markdown(extensions=[CodeHiliteExtension()])
        )
        with (
            patch.object(fenced_code, "_rust_code_highlight", self.extension),
            patch.object(fenced_code, "CodeHilite", return_value=None) as factory,
            self.assertLogs(level="ERROR") as logs,
        ):
            self.assertIn("&lt;x&gt;", processor.format_code(None, "<x>"))
        factory.assert_called_once()
        assert logs.records[0].exc_info is not None
        self.assertIs(logs.records[0].exc_info[0], AttributeError)

    def test_finish_failures_propagate_without_highlight_retry(self) -> None:
        for target in ["fromstring", "tostring"]:
            with self.subTest(target=target):
                processor = fenced_code.FencedBlockPreprocessor(Markdown())
                with (
                    patch.object(fenced_code, "_rust_code_highlight", self.extension),
                    patch.object(lxml.html, target, side_effect=ValueError("html")) as callback,
                    self.assertRaisesRegex(ValueError, "html"),
                ):
                    processor.format_code("js", "x")
                callback.assert_called_once()

    def test_unknown_lexer_is_the_only_suppressed_lookup_failure(self) -> None:
        processor = fenced_code.FencedBlockPreprocessor(Markdown())
        with (
            patch.object(fenced_code, "_rust_code_highlight", self.extension),
            patch.object(
                fenced_code, "find_lexer_class_by_name", side_effect=RuntimeError("lexer")
            ),
            self.assertRaisesRegex(RuntimeError, "lexer"),
        ):
            processor.format_code("js", "x")

    def test_no_language_does_not_parse_html(self) -> None:
        processor = fenced_code.FencedBlockPreprocessor(Markdown())
        with (
            patch.object(fenced_code, "_rust_code_highlight", self.extension),
            patch.object(lxml.html, "fromstring") as parse,
        ):
            self.assertEqual(
                processor.format_code(None, "<&"), "<pre><code>&lt;&amp;\n</code></pre>"
            )
        parse.assert_not_called()

    def test_old_wheel_and_partial_exports_use_python(self) -> None:
        for native in [None, SimpleNamespace(), SimpleNamespace(prepare_fenced_highlight=Mock())]:
            with self.subTest(native=native):
                processor = fenced_code.FencedBlockPreprocessor(Markdown())
                with patch.object(fenced_code, "_rust_code_highlight", native):
                    self.assertIn(
                        'data-code-language="JavaScript"', processor.format_code("js", "x")
                    )
                if native is not None and hasattr(native, "prepare_fenced_highlight"):
                    native.prepare_fenced_highlight.assert_not_called()
