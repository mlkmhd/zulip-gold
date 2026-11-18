import random
from contextlib import nullcontext
from unittest import TestCase, mock
from xml.etree.ElementTree import Element

from markdown import Markdown

from zerver.lib.markdown import ListIndentProcessor, OListProcessor, UListProcessor


class MarkdownListBlockTest(TestCase):
    def render(self, content: str, use_python: bool = False) -> str:
        engine = Markdown(extensions=["sane_lists"])
        engine.parser.blockprocessors.register(ListIndentProcessor(engine.parser), "indent", 90)
        engine.parser.blockprocessors.register(OListProcessor(engine.parser), "olist", 40)
        engine.parser.blockprocessors.register(UListProcessor(engine.parser), "ulist", 30)
        context = (
            mock.patch("zerver.lib.markdown._rust_markdown_list_blocks", None)
            if use_python
            else nullcontext()
        )
        with context:
            return engine.convert(content)

    def test_numbering_nesting_and_list_type_changes(self) -> None:
        self.assertEqual(
            self.render("۳. عنوان **پررنگ**\n۴. next"),
            '<ol start="۳">\n<li>عنوان <strong>پررنگ</strong></li>\n<li>next</li>\n</ol>',
        )
        for content in [
            "* first\n  * nested\n  * second\n* last",
            "1. first\n  * nested\n  * second\n2. last",
            "* first\n  1. nested\n  2. second\n* last",
            "* first\n\n* second\n\n  paragraph",
            "* first\n\n  * nested\n\n  * second",
            "* first\n1. continuation\n* last",
            "001. first\n۰۰۲. next",
            "+ first\n- second\n* third",
            "* <script>text</script>\n  > quote",
            "* first\n  * nested\n    * deeper\n\n* last",
            "* \ud800\n* fallback",
        ]:
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_generated_lists(self) -> None:
        rng = random.Random(0)
        lines = [
            "* item",
            "+ item",
            "- item",
            "12. item",
            "۳. item",
            "². text",
            "  * nested",
            "   2. nested",
            "    * deep",
            "plain text",
            "",
            " # heading",
            "*\ttab",
            "* \u001f\u0085text",
            "- \\* escaped",
            "> quote",
        ]
        for _ in range(3000):
            content = "\n".join(rng.choices(lines, k=rng.randrange(1, 15)))
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_invalid_items_preserve_python_errors(self) -> None:
        for processor_type in [OListProcessor, UListProcessor]:
            processor = processor_type(Markdown().parser)
            for block in ["", "plain", "  * nested", "  1. nested", "\ud800"]:
                with (
                    self.subTest(processor=processor_type, block=block),
                    self.assertRaises(IndexError),
                ):
                    processor.get_items(block)

    def test_unicode_digit_fallback(self) -> None:
        for digit in ["۳", "\U00011f50", "\U00016ac0", "\U0001e4f0", "\U00010d40"]:
            for content in [f"{digit}. item", f"* first\n  {digit}. nested", f"* {digit}"]:
                with self.subTest(content=content):
                    self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_rust_parser_is_used(self) -> None:
        import zerver.lib.markdown as markdown_module

        if markdown_module._rust_markdown_list_blocks is None:
            self.skipTest("Rust list block parsing is disabled")
        for processor_type, content in [
            (OListProcessor, "12. first\n13. next"),
            (UListProcessor, "* first\n* next"),
        ]:
            processor = processor_type(Markdown().parser)
            with (
                mock.patch.object(processor, "RE") as pattern,
                mock.patch.object(processor, "CHILD_RE") as child,
            ):
                pattern.match.side_effect = AssertionError("Python list test was used")
                child.match.side_effect = AssertionError("Python list item parsing was used")
                self.assertTrue(processor.test(Element("div"), content))
                self.assertEqual(processor.get_items(content), ["first", "next"])
                if processor_type is OListProcessor:
                    self.assertEqual(processor.STARTSWITH, "12")
