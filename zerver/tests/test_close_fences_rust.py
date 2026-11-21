from importlib import import_module
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import patch

from django.test import SimpleTestCase
from markdown import Markdown
from typing_extensions import override

from zerver.lib.markdown import fenced_code


class CloseFencesRustTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "close_unclosed_markdown_fences"):
            raise SkipTest("Rust extension does not support closing fences")

    def test_nested_fences_preserve_source_and_close_in_reverse_order(self) -> None:
        for content, language, suffix in [
            ("hello", None, ""),
            ("```py\n```quote\nbody", None, "\n```"),
            ("````quote\n~~~spoiler title\n```math\nx", None, "\n```\n~~~\n````"),
            ("```\n~~~py\ncode", "QuOtEd", "\n~~~\n```"),
            ("~~~spoiler\n```\n~~~quote", "quote", "\n```\n~~~"),
            ("```py\nx\n```\u3000", None, ""),
            ("```quote\ntext\n```", None, ""),
            ("```\ntext", "", "\n```"),
        ]:
            for native in [None, self.extension]:
                with (
                    self.subTest(content=content, language=language, native=bool(native)),
                    patch.object(fenced_code, "_rust_markdown_blocks", native),
                ):
                    self.assertEqual(
                        fenced_code.close_unclosed_fences(content, language), content + suffix
                    )

    def test_whitespace_is_normalized_only_for_fence_detection(self) -> None:
        for content in ["\x02```\x03\r\ncode", "```\tpy\rcode", "```quote\r\nhi\r\n"]:
            with patch.object(fenced_code, "_rust_markdown_blocks", self.extension):
                self.assertEqual(fenced_code.close_unclosed_fences(content), content + "\n```")

    def test_old_wheel_and_unsupported_inputs_delegate(self) -> None:
        class Content(str):
            __slots__ = ()

        for native in [
            SimpleNamespace(plan_markdown_blocks=self.extension.plan_markdown_blocks),
            self.extension,
        ]:
            for content, language in [
                ("\ud800\n```", None),
                ("```", "فارسی"),
                (Content("```"), None),
                ("```", Content("quote")),
                ("x" * (8 * 1024 * 1024) + "\n```", None),
            ]:
                with patch.object(fenced_code, "_rust_markdown_blocks", native):
                    self.assertEqual(
                        fenced_code.close_unclosed_fences(content, language), content + "\n```"
                    )
        self.assertIsNone(self.extension.close_unclosed_markdown_fences("\ud800"))
        self.assertIsNone(self.extension.close_unclosed_markdown_fences("```", "فارسی"))
        self.assertIsNone(
            self.extension.close_unclosed_markdown_fences("x" * (8 * 1024 * 1024 + 1))
        )

    def test_native_errors_are_not_retried(self) -> None:
        with (
            patch.object(fenced_code, "_rust_markdown_blocks", self.extension),
            patch.object(
                self.extension, "close_unclosed_markdown_fences", side_effect=ValueError("fences")
            ) as close,
            self.assertRaisesRegex(ValueError, "fences"),
        ):
            fenced_code.close_unclosed_fences("```", "quote")
        close.assert_called_once_with("```", "quote")

    def test_native_path_does_not_construct_python_handlers(self) -> None:
        with (
            patch.object(fenced_code, "_rust_markdown_blocks", self.extension),
            patch.object(fenced_code, "UnclosedFenceFinder", side_effect=AssertionError),
        ):
            self.assertEqual(fenced_code.close_unclosed_fences("```math\nx"), "```math\nx\n```")

    def test_following_reply_renders_outside_nested_quotes(self) -> None:
        results = []
        for native in [None, self.extension]:
            with patch.object(fenced_code, "_rust_markdown_blocks", native):
                content = fenced_code.close_unclosed_fences("````quote\n```python\nprint('<&>')")
                results.append(
                    Markdown(extensions=[fenced_code.FencedCodeExtension()]).convert(
                        content + "\n\nThanks!"
                    )
                )
        self.assertEqual(*results)
        self.assertTrue(results[0].endswith("</blockquote>\n<p>Thanks!</p>"))
