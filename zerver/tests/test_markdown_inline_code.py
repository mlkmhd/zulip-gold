import random
import re
from contextlib import nullcontext
from typing import SupportsIndex, cast
from unittest import TestCase, mock

import markdown
from typing_extensions import override

from zerver.actions.user_groups import check_add_user_group
from zerver.lib.markdown import BacktickInlineProcessor, render_message_markdown
from zerver.lib.markdown.inline_code import BacktickPattern, backtick_pattern
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message
from zerver.models.clients import get_client


class MarkdownInlineCodeTest(TestCase):
    def render(self, content: str, use_python: bool = False) -> str:
        engine = markdown.Markdown()
        context = (
            mock.patch("zerver.lib.markdown.inline_code._rust_inline_code", None)
            if use_python
            else nullcontext()
        )
        with context:
            processor = BacktickInlineProcessor(markdown.inlinepatterns.BACKTICK_RE)
            engine.inlinePatterns.register(processor, "backtick", 190)
            return engine.convert(content)

    def test_code_text_and_html_escaping(self) -> None:
        self.assertEqual(
            self.render("before ` عنوان <script>&copy;</script> ` after"),
            "<p>before <code> عنوان &lt;script&gt;&amp;copy;&lt;/script&gt; </code> after</p>",
        )
        self.assertEqual(self.render("` **bold** `"), "<p><code> **bold** </code></p>")
        for content in [
            "`one\ntwo`",
            "```a``b`",
            "`a``b`",
            "``a`b``",
            "`unclosed",
            "before `one` middle `two` after",
            r"\`one` \\`two` \\\`three`",
            "`@**Zoe** @*all* #**general** https://zulip.com :smile: $x$`",
            "📝 `عنوان` and ``📝 `value` ``",
            "`\ud800` and `fallback`",
        ]:
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_match_captures_and_offsets(self) -> None:
        fallback = re.compile(markdown.inlinepatterns.BACKTICK_RE, re.DOTALL | re.UNICODE)
        rng = random.Random(0)
        samples = [
            "📝 `a` then \\``b`",
            "\\\\`a`",
            "\\``a`",
            "`\ud800` and `fallback`",
            "````a```b``c`",
            "",
        ]
        samples.extend(
            "".join(rng.choices("`\\ a\n\r📝عنوان\x02\x03", k=rng.randrange(60)))
            for _ in range(3000)
        )
        for data in samples:
            for position in [-1, 0, 1, len(data) // 2, len(data), len(data) + 1]:
                expected = [
                    (m.start(), m.end(), tuple(m.group(i) for i in range(4)))
                    for m in fallback.finditer(data, position)
                ]
                actual = [
                    (m.start(), m.end(), tuple(m.group(i) for i in range(4)))
                    for m in backtick_pattern(fallback).finditer(data, position)
                ]
                with self.subTest(data=data, position=position):
                    self.assertEqual(actual, expected)

    def test_oversized_positions_keep_python_errors(self) -> None:
        fallback = re.compile(markdown.inlinepatterns.BACKTICK_RE, re.DOTALL | re.UNICODE)
        for position in [-(2**100), 2**100]:
            with self.assertRaises(OverflowError):
                list(backtick_pattern(fallback).finditer("`code`", position))

    def test_string_subclass_and_index_keep_the_reference_dispatch(self) -> None:
        class Content(str):
            __slots__ = ()

            @override
            def __getitem__(self, index: SupportsIndex | slice) -> str:
                raise AssertionError("regular expression should not call subclass slicing")

        class Position:
            def __index__(self) -> int:
                return 0

        fallback = re.compile(markdown.inlinepatterns.BACKTICK_RE, re.DOTALL | re.UNICODE)
        for source, position in (
            (Content("x `code` y"), 0),
            ("x `code` y", cast("int", Position())),
        ):
            actual = list(backtick_pattern(fallback).finditer(source, position))
            self.assertEqual([(match.start(), match.group(3)) for match in actual], [(2, "code")])

    def test_generated_renderings(self) -> None:
        rng = random.Random(1)
        for _ in range(1000):
            data = "".join(rng.choices("`\\ *<>\n📝عنوان&", k=rng.randrange(60)))
            self.assertEqual(self.render(data), self.render(data, use_python=True))

    def test_custom_pattern_keeps_python_matcher(self) -> None:
        processor = BacktickInlineProcessor(r"(a)(b)(c)")
        self.assertIsInstance(processor.compiled_re, re.Pattern)

    def test_rust_matcher_is_used(self) -> None:
        from zerver.lib.markdown import inline_code

        if inline_code._rust_inline_code is None:
            self.skipTest("Rust inline code parser is disabled")
        processor = BacktickInlineProcessor(markdown.inlinepatterns.BACKTICK_RE)
        self.assertIsInstance(processor.compiled_re, BacktickPattern)
        with (
            mock.patch.object(
                BacktickPattern, "finditer", wraps=processor.compiled_re.finditer
            ) as find,
            mock.patch.object(
                markdown.inlinepatterns.BacktickInlineProcessor,
                "handleMatch",
                side_effect=AssertionError,
            ),
        ):
            result = self.render("`@**Zoe** <&> `")
            self.assertEqual(result, "<p><code>@**Zoe** &lt;&amp;&gt; </code></p>")
            find.assert_called()
            self.assertEqual(self.render(r"\\`unclosed"), "<p>\\`unclosed</p>")


class MarkdownInlineCodeRenderingTest(ZulipTestCase):
    def test_code_suppresses_inline_notifications_and_previews(self) -> None:
        sender = self.example_user("othello")
        hamlet = self.example_user("hamlet")
        group = check_add_user_group(sender.realm, "backend", [sender], acting_user=sender)
        message = Message(sender=sender, sending_client=get_client("test"), realm=sender.realm)
        syntax = f"@**|{hamlet.id}** @*{group.name}* @**all** @**topic**"
        for delimiter, body in [("`", syntax), ("``", syntax + " `nested` ")]:
            with self.subTest(delimiter=delimiter):
                content = f"{delimiter}{body} <&> https://zulip.com{delimiter}"
                rendered = render_message_markdown(message, content)
                self.assertEqual(
                    rendered.rendered_content,
                    f"<p><code>{body} &lt;&amp;&gt; https://zulip.com</code></p>",
                )
                self.assertEqual(rendered.mentions_user_ids, set())
                self.assertEqual(rendered.mentions_user_group_ids, set())
                self.assertFalse(rendered.mentions_stream_wildcard)
                self.assertFalse(rendered.mentions_topic_wildcard)
                self.assertEqual(rendered.links_for_preview, set())
                outside = render_message_markdown(message, content + "\n\n" + syntax)
                self.assertEqual(outside.mentions_user_ids, {hamlet.id})
                self.assertEqual(outside.mentions_user_group_ids, {group.id})
                self.assertTrue(outside.mentions_stream_wildcard)
                self.assertTrue(outside.mentions_topic_wildcard)
