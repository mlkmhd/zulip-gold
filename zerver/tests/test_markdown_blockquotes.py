import random
from contextlib import nullcontext
from unittest import TestCase, mock
from xml.etree.ElementTree import Element, tostring

from markdown import Markdown

from zerver.lib.markdown import BlockQuoteProcessor


class MarkdownBlockQuoteTest(TestCase):
    def render(self, content: str, use_python: bool = False) -> str:
        engine = Markdown()
        engine.parser.blockprocessors.register(BlockQuoteProcessor(engine.parser), "quote", 55)
        context = (
            mock.patch("zerver.lib.markdown._rust_markdown_blockquotes", None)
            if use_python
            else nullcontext()
        )
        with context:
            return engine.convert(content)

    def test_nested_quotes_and_separate_blocks(self) -> None:
        self.assertEqual(
            self.render("before\n> عنوان **پررنگ**\n>> nested\n\n> second"),
            "<p>before</p>\n<blockquote>\n<p>عنوان <strong>پررنگ</strong></p>\n"
            "<blockquote>\n<p>nested</p>\n</blockquote>\n</blockquote>\n"
            "<blockquote>\n<p>second</p>\n</blockquote>",
        )
        for content in [
            ">",
            ">\n>",
            ">\n\ntext",
            ">\ntext",
            ">\n> text",
            ">\n\n> text",
            "   > indented\n    > continuation",
            ">\ttext\n>\u001f\u0085\n> next",
            "* item\n\n  > quote\n  > next",
            "> * item\n>   * nested\n>\n> paragraph",
            "> @**User|1** and @*Group|2*\n>> @_**Silent|3**",
            "> [link](https://zulip.com) <script>text</script>",
            "> \ud800\n> fallback",
        ]:
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_generated_blocks(self) -> None:
        rng = random.Random(0)
        alphabet = ">#-* \n\\\t\rعنوان📝<>*&\u001c\u0085\u2028"
        for _ in range(3000):
            content = "".join(rng.choices(alphabet, k=rng.randrange(50)))
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_marker_cleaning_and_surrogate_fallback(self) -> None:
        processor = BlockQuoteProcessor(Markdown().parser)
        for line in [
            "> content",
            ">\tcontent",
            ">\u001f\u0085",
            "\u001c>\u0085",
            "\n> content",
            "> first\n> second",
            ">\n\n> second",
            "before\n> content",
            "> @**User|1** and @*Group|2*",
            "> \ud800 @**User|1**",
        ]:
            with self.subTest(line=line):
                actual = processor.clean(line)
                with mock.patch("zerver.lib.markdown._rust_markdown_blockquotes", None):
                    expected = processor.clean(line)
                self.assertEqual(actual, expected)

    def test_forced_run_without_a_quote(self) -> None:
        for block in ["plain text", "\ud800", ">\n\ntext"]:
            results = []
            for use_python in [False, True]:
                engine = Markdown()
                parent = Element("div")
                context = (
                    mock.patch("zerver.lib.markdown._rust_markdown_blockquotes", None)
                    if use_python
                    else nullcontext()
                )
                with context:
                    blocks = [block]
                    BlockQuoteProcessor(engine.parser).run(parent, blocks)
                self.assertEqual(blocks, [])
                self.assertEqual(engine.parser.state, [])
                results.append(tostring(parent))
            self.assertEqual(*results)

    def test_recursion_limit(self) -> None:
        processor = BlockQuoteProcessor(Markdown().parser)
        for limited in [False, True]:
            with mock.patch("markdown.util.nearing_recursion_limit", return_value=limited):
                self.assertEqual(processor.test(Element("div"), "> text"), not limited)
                self.assertFalse(processor.test(Element("div"), "plain text"))

    def test_rust_parser_is_used(self) -> None:
        import zerver.lib.markdown as markdown_module

        if markdown_module._rust_markdown_blockquotes is None:
            self.skipTest("Rust blockquote parsing is disabled")
        with mock.patch.object(BlockQuoteProcessor, "RE") as pattern:
            pattern.search.side_effect = AssertionError("Python quote search was used")
            pattern.match.side_effect = AssertionError("Python quote cleaning was used")
            self.assertEqual(
                self.render("> content"), "<blockquote>\n<p>content</p>\n</blockquote>"
            )
