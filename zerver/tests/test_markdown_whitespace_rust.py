import os
from importlib import import_module
from typing import cast
from unittest import TestCase
from unittest.mock import patch

from markdown import Markdown
from markdown.preprocessors import NormalizeWhitespace

from zerver.lib.markdown import whitespace
from zerver.lib.native_backend import supports_default_rust_backend


class MarkdownWhitespaceRustBoundaryTest(TestCase):
    def test_all_surrogate_code_points_preserve_python_tabs_and_line_structure(self) -> None:
        try:
            native = import_module("_zulip_backend")
        except ImportError:
            self.skipTest("Wheel is absent")
        if getattr(native, "MARKDOWN_WHITESPACE_API", None) != 1:
            self.skipTest("Wheel predates surrogate-safe whitespace normalization")
        for width in (-1, 0, 1, 4, 8):
            original = NormalizeWhitespace(Markdown(tab_length=width))
            for code in range(0xD800, 0xE000):
                lines = [
                    f"\tمتن{chr(code)}\t😀\r\x02",
                    "\x03\n   ",
                    " \t X ",
                    "",
                ]
                self.assertEqual(
                    native.normalize_markdown_whitespace(lines, width), original.run(lines)
                )

    def test_default_dispatch_and_unsupported_width_match_reference(self) -> None:
        native = whitespace._normalize_markdown_whitespace
        enabled = os.environ.get("ZULIP_RUST_MARKDOWN_WHITESPACE") == "1" or (
            supports_default_rust_backend()
            and getattr(whitespace._rust_markdown_whitespace, "MARKDOWN_WHITESPACE_API", None) == 1
        )
        self.assertEqual(callable(native), enabled)
        lines = ["abc\t", "   ", "a\r", "\x02\n", " b"]
        original = Markdown(tab_length=4)
        with (
            patch.object(whitespace, "_normalize_markdown_whitespace", wraps=native)
            if enabled
            else patch.object(whitespace, "_normalize_markdown_whitespace", None)
        ) as formatter:
            self.assertEqual(
                whitespace.ZulipNormalizeWhitespace(original).run(lines),
                NormalizeWhitespace(original).run(lines),
            )
            if formatter is not None:
                formatter.assert_called_once_with(lines, 4)
                formatter.reset_mock()
            # Keep uncommon widths on the original reference implementation.
            unusual = Markdown(tab_length=4097)
            self.assertEqual(
                whitespace.ZulipNormalizeWhitespace(unusual).run(lines),
                NormalizeWhitespace(unusual).run(lines),
            )
            if formatter is not None:
                formatter.assert_not_called()

    def test_decline_keeps_reference_and_original_exception(self) -> None:
        original = Markdown(tab_length=4)
        lines = ("a\tb", "c")
        with patch.object(whitespace, "_normalize_markdown_whitespace", return_value=None):
            self.assertEqual(
                whitespace.ZulipNormalizeWhitespace(original).run(list(lines)),
                NormalizeWhitespace(original).run(list(lines)),
            )
            with self.assertRaisesRegex(TypeError, "expected str instance"):
                whitespace.ZulipNormalizeWhitespace(original).run(cast("list[str]", ["a", 2]))
