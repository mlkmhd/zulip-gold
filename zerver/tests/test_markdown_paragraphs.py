import random
from unittest import TestCase, mock
from xml.etree.ElementTree import Element, SubElement, tostring

from markdown import Markdown
from markdown.blockprocessors import ParagraphProcessor as PythonParagraphProcessor
from typing_extensions import override

from zerver.lib.markdown import ParagraphProcessor


class MarkdownParagraphTest(TestCase):
    def render(self, content: str, use_python: bool = False) -> str:
        engine = Markdown()
        processor = PythonParagraphProcessor if use_python else ParagraphProcessor
        engine.parser.blockprocessors.register(processor(engine.parser), "paragraph", 10)
        return engine.convert(content)

    def test_paragraphs_and_list_continuations(self) -> None:
        self.assertEqual(
            self.render("  عنوان **پررنگ**  "), "<p>عنوان <strong>پررنگ</strong>  </p>"
        )
        for content in [
            "\u001c\u0085\u3000",
            "\u001f عنوان  ",
            "one\ntwo\n\nthree",
            "* first\n* second",
            "* # Heading\n  continuation",
            "> before\n> after",
            "* first\n\n    paragraph\n\n* second",
            "<script>alert('x')</script>",
            "[link](https://zulip.com) & **bold**",
        ]:
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_generated_blocks(self) -> None:
        rng = random.Random(0)
        alphabet = "#-* \n\\\t\rعنوان📝<>*&\u001c\u0085\u2028"
        for _ in range(3000):
            content = "".join(rng.choices(alphabet, k=rng.randrange(50)))
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_tight_list_text_and_tail(self) -> None:
        for block in ["  next  ", "\u001f next", "\u001c\u0085", "\ud800"]:
            for tight_list in [False, True]:
                for has_sibling in [False, True]:
                    for previous in [None, "", "before", "\ud800"]:
                        results = []
                        for processor in [PythonParagraphProcessor, ParagraphProcessor]:
                            engine = Markdown()
                            if tight_list:
                                engine.parser.state.set("list")
                            parent = Element("li")
                            if has_sibling:
                                SubElement(parent, "h1").tail = previous
                            else:
                                parent.text = previous
                            blocks = [block, "remaining"]
                            processor(engine.parser).run(parent, blocks)
                            results.append((tostring(parent), blocks))
                        with self.subTest(
                            block=block,
                            tight_list=tight_list,
                            has_sibling=has_sibling,
                            previous=previous,
                        ):
                            self.assertEqual(results[0], results[1])

    def test_rust_path(self) -> None:
        from zerver.lib import markdown

        if markdown._rust_markdown_paragraphs is None:
            self.skipTest("Rust paragraph planner is disabled")
        with mock.patch.object(PythonParagraphProcessor, "run", side_effect=AssertionError):
            self.assertEqual(self.render("  text"), "<p>text</p>")

    def test_paragraph_subclass_uses_its_own_last_child(self) -> None:
        class CustomParagraph(ParagraphProcessor):
            @override
            def lastChild(self, parent: Element) -> Element | None:
                return parent[0]

        engine = Markdown()
        engine.parser.state.set("list")
        parent = Element("li")
        first, second = SubElement(parent, "strong"), SubElement(parent, "em")
        remaining = [" text "]
        CustomParagraph(engine.parser).run(parent, remaining)
        self.assertEqual(first.tail, "\n text ")
        self.assertIsNone(second.tail)
        self.assertEqual(remaining, [])
