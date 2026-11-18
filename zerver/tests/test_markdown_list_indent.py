import random
from contextlib import nullcontext
from unittest import TestCase, mock
from xml.etree.ElementTree import Element, SubElement

from markdown import Markdown
from markdown.blockprocessors import ListIndentProcessor as PythonListIndentProcessor

from zerver.lib.markdown import ListIndentProcessor, OListProcessor, UListProcessor


class MarkdownListIndentTest(TestCase):
    def render(self, content: str, use_python: bool = False) -> str:
        engine = Markdown(extensions=["sane_lists"])
        engine.parser.blockprocessors.register(ListIndentProcessor(engine.parser), "indent", 90)
        engine.parser.blockprocessors.register(OListProcessor(engine.parser), "olist", 40)
        engine.parser.blockprocessors.register(UListProcessor(engine.parser), "ulist", 30)
        context = (
            mock.patch("zerver.lib.markdown._rust_markdown_list_indent", None)
            if use_python
            else nullcontext()
        )
        with context:
            return engine.convert(content)

    def test_nested_lists_and_indented_code(self) -> None:
        for content in [
            "* first\n\n  * nested\n\n  * second\n* last",
            "1. first\n\n  * nested\n    * deeper\n\n    paragraph\n\n2. next",
            "* first\n\n      code\n      next\n\n  paragraph",
            "* * subitem\n    * next\n\n  * last",
            "* # Heading\n\n  paragraph\n\n    * nested",
            "* first\n\n  > quote\n  > next",
            "* عنوان\n\n  پاراگراف **پررنگ**\n\n  * بعدی",
            "* first\n\n  \ud800",
        ]:
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_list_parent_selection(self) -> None:
        rng = random.Random(0)
        processor = ListIndentProcessor(Markdown().parser)
        for _ in range(3000):
            parent = Element(rng.choice(["div", "li", "ul", "ol"]))
            child = parent
            for _ in range(rng.randrange(10)):
                SubElement(child, "p").text = "earlier sibling"
                child = SubElement(child, rng.choice(["li", "ul", "ol", "p", "blockquote"]))
            block = " " * rng.randrange(20) + rng.choice(["text", "* item", "۳. item", "\ud800"])
            tight_list = rng.choice([False, True])
            if tight_list:
                processor.parser.state.set("list")
            actual_level, actual_parent = processor.get_level(parent, block)
            with mock.patch("zerver.lib.markdown._rust_markdown_list_indent", None):
                expected_level, expected_parent = processor.get_level(parent, block)
            if tight_list:
                processor.parser.state.reset()
            self.assertEqual(actual_level, expected_level)
            self.assertIs(actual_parent, expected_parent)

    def test_detab_keeps_dedented_lines(self) -> None:
        processor = ListIndentProcessor(Markdown().parser)
        for block in [
            "    title\n  next\n\n    \u001ftext\n\tcode\n",
            "    عنوان\n   shorter\n\u3000space\n",
            "  \ud800\n    fallback",
            "",
        ]:
            for level in [-2, -1, 0, 1, 2, 3]:
                with self.subTest(block=block, level=level):
                    actual = processor.looseDetab(block, level)
                    with mock.patch("zerver.lib.markdown._rust_markdown_list_indent", None):
                        expected = processor.looseDetab(block, level)
                    self.assertEqual(actual, expected)

    def test_rust_parser_is_used(self) -> None:
        import zerver.lib.markdown as markdown_module

        if markdown_module._rust_markdown_list_indent is None:
            self.skipTest("Rust list indentation is disabled")
        with (
            mock.patch.object(PythonListIndentProcessor, "get_level") as get_level,
            mock.patch.object(PythonListIndentProcessor, "looseDetab") as detab,
        ):
            get_level.side_effect = AssertionError("Python list parent selection was used")
            detab.side_effect = AssertionError("Python list detab was used")
            self.assertEqual(
                self.render("* first\n\n  * nested"),
                "<ul>\n<li>\n<p>first</p>\n<ul>\n<li>nested</li>\n</ul>\n</li>\n</ul>",
            )
