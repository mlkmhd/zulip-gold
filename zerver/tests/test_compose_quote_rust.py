from importlib import import_module
from os import environ
from random import Random
from types import ModuleType, SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from markdown import Markdown

from zerver.lib import compose_reply
from zerver.lib.compose_reply import MaxContentLengths
from zerver.lib.markdown import fenced_code

SUFFIX = "\n[message truncated]"


def extension() -> ModuleType:
    try:
        native = import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        raise SkipTest("Rust extension is not installed") from None
    for name in ("allocate_compose_quote_lengths", "truncate_compose_quote_text"):
        if not callable(getattr(native, name, None)):
            raise SkipTest("Rust extension does not support quote composition sizing")
    return native


class ComposeQuoteRustTest(SimpleTestCase):
    def test_budget_matches_quote_minimum_and_syntax_overflow(self) -> None:
        native = extension()
        rng = Random(64112)
        for _ in range(500):
            reply, quote, syntax, limit = (rng.randrange(-10, 4000) for _ in range(4))
            available = max(limit - syntax, 0)
            quoted = min(quote, max(available - reply, min(1000, available)))
            expected = (min(reply, available - quoted), quoted)
            actual = native.allocate_compose_quote_lengths(reply, quote, syntax, limit)
            self.assertEqual(actual, expected)
        self.assertIsNone(native.allocate_compose_quote_lengths(True, 1, 2, 3))
        self.assertIsNone(native.allocate_compose_quote_lengths(2**100, 1, 2, 3))

    def test_retry_uses_unicode_codepoints_and_existing_closer(self) -> None:
        native = extension()
        calls: list[str] = []

        def closer(text: str) -> str:
            calls.append(text)
            return text + "\n\x60\x60\x60"

        topic = "\x60\x60\x60\n" + "سلام 🙂" * 20
        result = native.truncate_compose_quote_text(topic, 44, closer)
        self.assertEqual(calls, [topic[:24], topic[:20]])
        self.assertEqual(result, topic[:20] + "\n\x60\x60\x60" + SUFFIX)
        self.assertEqual(native.truncate_compose_quote_text(topic, 5, closer), SUFFIX)
        self.assertIs(native.truncate_compose_quote_text(topic, 500, closer), topic)
        self.assertIsNone(native.truncate_compose_quote_text("\ud800", 0, closer))
        self.assertIsNone(native.truncate_compose_quote_text(topic, True, closer))

        def failing_closer(text: str) -> str:
            raise ValueError("synthetic closer failure")

        with self.assertRaisesRegex(ValueError, "^synthetic closer failure$"):
            native.truncate_compose_quote_text(topic, 44, failing_closer)


class ComposeQuoteDjangoDispatchRustTest(SimpleTestCase):
    @override_settings(MAX_MESSAGE_LENGTH=1500)
    def test_dispatch_keeps_exact_trusted_parser_and_rendered_composition(self) -> None:
        native = extension()
        enabled = environ.get("ZULIP_RUST_COMPOSE_QUOTE") == "1"
        self.assertIs(compose_reply._rust_compose_quote, native if enabled else None)
        reply = "Thanks! " * 240
        quoted = "```python\n" + "print('سلام🙂')\n" * 160 + "```"

        def compose() -> str:
            return compose_reply.compose_quote_and_reply_for_channel_message(
                reply_content=reply,
                quote_content=quoted,
                quote_message_sender_silent_mention="@_**Synthetic Sender**",
                quote_message_url="#narrow/channel/1-synthetic/topic/check/near/2",
                quote_message_topic_pretty_link="#**Synthetic>check**",
            )

        with patch.object(compose_reply, "_rust_compose_quote", None):
            expected = compose()

        with (
            patch.object(
                native,
                "allocate_compose_quote_lengths",
                wraps=native.allocate_compose_quote_lengths,
            ) as allocate,
            patch.object(
                native, "truncate_compose_quote_text", wraps=native.truncate_compose_quote_text
            ) as clip,
        ):
            actual = compose()
        self.assertEqual(actual, expected)
        self.assertLessEqual(len(actual), 1500)
        self.assertEqual(actual.count(SUFFIX), 2)
        rendered = Markdown(extensions=[fenced_code.FencedCodeExtension()]).convert(actual)
        self.assertGreater(rendered.index("<p>Thanks!"), rendered.index("</blockquote>"))
        if enabled:
            allocate.assert_called_once()
            clip.assert_called_once()
            self.assertEqual(allocate.call_args.args[:2], (len(reply), len(quoted)))
            self.assertEqual(allocate.call_args.args[3], 1500)
            self.assertEqual(clip.call_args.args[:2], (quoted, 1000))
            self.assertIs(clip.call_args.args[2], fenced_code.close_unclosed_fences)
        else:
            allocate.assert_not_called()
            clip.assert_not_called()

    @override_settings(MAX_MESSAGE_LENGTH=30)
    def test_absent_older_partial_and_declined_helpers_are_independent(self) -> None:
        allocate = Mock(return_value=(0, 0))
        clip = Mock(return_value="")
        for backend, limits, expected_quote in (
            (None, MaxContentLengths(3, 4), "short"),
            (SimpleNamespace(), MaxContentLengths(3, 4), "short"),
            (
                SimpleNamespace(allocate_compose_quote_lengths=allocate),
                MaxContentLengths(0, 0),
                "short",
            ),
            (SimpleNamespace(truncate_compose_quote_text=clip), MaxContentLengths(3, 4), ""),
            (
                SimpleNamespace(
                    allocate_compose_quote_lengths=lambda *args: None,
                    truncate_compose_quote_text=lambda *args: None,
                ),
                MaxContentLengths(3, 4),
                "short",
            ),
        ):
            with patch.object(compose_reply, "_rust_compose_quote", backend):
                self.assertEqual(
                    compose_reply.get_measurements_for_quote_and_reply_content(3, 4, 1), limits
                )
                self.assertEqual(compose_reply.truncate_quote_content("short", 30), expected_quote)
        allocate.assert_called_once_with(3, 4, 1, 30)
        clip.assert_called_once_with("short", 30, fenced_code.close_unclosed_fences)

    @override_settings(MAX_MESSAGE_LENGTH=40)
    def test_changed_constants_nonstandard_limit_and_closer_stay_python(self) -> None:
        allocate = Mock()
        clip = Mock()
        backend = SimpleNamespace(
            allocate_compose_quote_lengths=allocate, truncate_compose_quote_text=clip
        )
        quote = "```\n" + "سلام🙂" * 20
        patched_closer = Mock(side_effect=lambda part: part + "\n```")
        with patch.object(compose_reply, "_rust_compose_quote", backend):
            with patch.object(compose_reply, "QUOTE_CONTENT_MINIMUM_LENGTH", 5):
                self.assertEqual(
                    compose_reply.get_measurements_for_quote_and_reply_content(100, 100, 1),
                    MaxContentLengths(34, 5),
                )
            with override_settings(MAX_MESSAGE_LENGTH=40.5):
                self.assertEqual(
                    compose_reply.get_measurements_for_quote_and_reply_content(3, 4, 1),
                    MaxContentLengths(3, 4),
                )
            with patch.object(compose_reply, "TRUNCATION_MESSAGE", "!"):
                self.assertEqual(compose_reply.truncate_quote_content("abcdef", 4), "abc!")
            with patch.object(compose_reply, "close_unclosed_fences", patched_closer):
                self.assertEqual(
                    compose_reply.truncate_quote_content(quote, 44),
                    quote[:20] + "\n```" + SUFFIX,
                )
        self.assertEqual(
            [call.args[0] for call in patched_closer.call_args_list], [quote[:24], quote[:20]]
        )
        allocate.assert_not_called()
        clip.assert_not_called()

    def test_unsupported_data_preserves_legacy_results_and_errors(self) -> None:
        native = extension()
        invalid_utf8 = "\ud800"
        for backend in (None, native):
            with (
                patch.object(compose_reply, "_rust_compose_quote", backend),
                override_settings(MAX_MESSAGE_LENGTH=2**100),
            ):
                self.assertIs(compose_reply.truncate_quote_content(invalid_utf8, 1), invalid_utf8)
                self.assertEqual(
                    compose_reply.get_measurements_for_quote_and_reply_content(3, 4, 1),
                    MaxContentLengths(3, 4),
                )

    def test_trusted_parser_exception_propagates_without_retry(self) -> None:
        native = extension()
        quote = "```\n" + "quote" * 30
        for backend in (None, native):
            with (
                patch.object(compose_reply, "_rust_compose_quote", backend),
                patch.object(fenced_code, "_rust_markdown_blocks", None),
                patch.object(
                    fenced_code.UnclosedFenceFinder,
                    "get_unclosed_fences",
                    side_effect=ValueError("synthetic parser failure"),
                ) as parser,
                self.assertRaisesRegex(ValueError, "^synthetic parser failure$"),
            ):
                compose_reply.truncate_quote_content(quote, 44)
            parser.assert_called_once_with(quote[:24], None)
