from importlib import import_module
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import patch

from django.test import SimpleTestCase
from markdown import Markdown
from typing_extensions import override

from zerver.lib.compose_reply import compose_quote_and_reply_for_channel_message
from zerver.lib.markdown import fenced_code


class UnusedFenceRustTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "get_unused_markdown_fence"):
            raise SkipTest("Rust extension does not support unused fences")
        patcher = patch.object(fenced_code, "_rust_quote_delimiter", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fence_uses_only_backticks_after_up_to_three_ascii_spaces(self) -> None:
        for content, length in [
            ("", 3),
            ("``", 3),
            ("```", 4),
            ("   `````python", 6),
            ("    `````````", 3),
            ("\t``````", 3),
            ("\u00a0``````", 3),
            ("text\n  `````suffix\n```", 6),
            ("~~~quote\n```\n````\n~~~", 5),
        ]:
            for native in [None, self.extension]:
                with (
                    self.subTest(content=content, native=bool(native)),
                    patch.object(fenced_code, "_rust_markdown_fences", native),
                ):
                    self.assertEqual(fenced_code.get_unused_fence(content), "`" * length)

    def test_only_lf_starts_a_new_line(self) -> None:
        with patch.object(fenced_code, "_rust_markdown_fences", self.extension):
            for separator in ["\r", "\u0085", "\u2028", "\u2029"]:
                self.assertEqual(fenced_code.get_unused_fence("text" + separator + "````"), "```")
            self.assertEqual(fenced_code.get_unused_fence("text\r\n````"), "`````")

    def test_old_wheel_unsupported_strings_and_subclasses_delegate(self) -> None:
        class Content(str):
            __slots__ = ()

        for native in [SimpleNamespace(), self.extension]:
            for content in ["\ud800\n```", "x" * (8 * 1024 * 1024) + "\n```", Content("  ```")]:
                with patch.object(fenced_code, "_rust_markdown_fences", native):
                    self.assertEqual(fenced_code.get_unused_fence(content), "````")
        self.assertIsNone(self.extension.get_unused_markdown_fence("\ud800"))
        self.assertIsNone(self.extension.get_unused_markdown_fence("x" * (8 * 1024 * 1024 + 1)))

    def test_native_choice_uses_the_same_scanner_as_reminder_quotes(self) -> None:
        content = "text\n   `````python\nhello\n    ````````\n\u2028```````"
        with patch.object(fenced_code, "_rust_markdown_fences", self.extension):
            fence = fenced_code.get_unused_fence(content)
        self.assertEqual(
            self.extension.append_reminder_quote("context\n", content, 1000),
            f"context\n{fence}quote\n{content}\n{fence}",
        )

    def test_native_error_propagates_without_retry(self) -> None:
        with (
            patch.object(fenced_code, "_rust_markdown_fences", self.extension),
            patch.object(
                self.extension, "get_unused_markdown_fence", side_effect=ValueError("fence")
            ) as choose,
            self.assertRaisesRegex(ValueError, "fence"),
        ):
            fenced_code.get_unused_fence("```")
        choose.assert_called_once_with("```")

    def test_composed_quote_keeps_nested_code_and_reply_outside_it(self) -> None:
        results = []
        for native in [None, self.extension]:
            with patch.object(fenced_code, "_rust_markdown_fences", native):
                composed = compose_quote_and_reply_for_channel_message(
                    reply_content="Thanks!",
                    quote_content="```python\nprint('<&>')\n```\n\n````quote\ntext\n````",
                    quote_message_sender_silent_mention="@_**Iago**",
                    quote_message_url="#narrow/channel/9-Verona/topic/hello/near/15",
                    quote_message_topic_pretty_link="#**Verona>hello**",
                )
                rendered = Markdown(extensions=[fenced_code.FencedCodeExtension()]).convert(
                    composed
                )
                results.append((composed, rendered))
        self.assertEqual(*results)
        self.assertIn("`````quote\n", results[0][0])
        self.assertTrue(results[0][1].endswith("</blockquote>\n<p>Thanks!</p>"))


class FencedQuoteDelimiterContractTest(SimpleTestCase):
    def test_quote_with_backticks_keeps_the_reply_outside_the_quote(self) -> None:
        composed = compose_quote_and_reply_for_channel_message(
            reply_content="Thanks!",
            quote_content="```python\nprint('hello')\n```",
            quote_message_sender_silent_mention="@_**Iago**",
            quote_message_url="#narrow/channel/9-Verona/topic/hello/near/15",
            quote_message_topic_pretty_link="#**Verona>hello**",
        )
        self.assertIn("````quote\n", composed)
        rendered = Markdown(extensions=[fenced_code.FencedCodeExtension()]).convert(composed)
        self.assertTrue(rendered.endswith("</blockquote>\n<p>Thanks!</p>"))

    def test_default_uses_rust_for_surrogates_and_large_input(self) -> None:
        backend = fenced_code._rust_quote_delimiter
        if getattr(backend, "FENCED_QUOTE_DELIMITER_API", None) != 1:
            self.skipTest("Matching fenced quote delimiter contract is not available")

        source = "x" * (8 * 1024 * 1024) + "\ud800\n   ````"
        with patch.object(
            backend, "choose_fenced_quote_delimiter", wraps=backend.choose_fenced_quote_delimiter
        ) as choose:
            self.assertEqual(fenced_code.get_unused_fence(source), "`````")
        choose.assert_called_once_with(source)
