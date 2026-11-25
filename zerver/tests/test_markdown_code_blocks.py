import random
from contextlib import nullcontext
from unittest import TestCase, mock
from xml.etree.ElementTree import Element, SubElement, tostring

from markdown import Markdown
from markdown.blockprocessors import CodeBlockProcessor as PythonCodeBlockProcessor
from markdown.blockprocessors import EmptyBlockProcessor as PythonEmptyBlockProcessor
from markdown.util import AtomicString

from zerver.lib.markdown.code_blocks import CodeBlockProcessor, EmptyBlockProcessor


class MarkdownCodeBlockTest(TestCase):
    def render(self, content: str, use_python: bool = False) -> str:
        engine = Markdown(tab_length=2)
        context = (
            mock.patch("zerver.lib.markdown.code_blocks._rust_code_blocks", None)
            if use_python
            else nullcontext()
        )
        with context:
            engine.parser.blockprocessors.register(CodeBlockProcessor(engine.parser), "code", 80)
            engine.parser.blockprocessors.register(EmptyBlockProcessor(engine.parser), "empty", 100)
            return engine.convert(content)

    def test_code_blocks_preserve_whitespace_and_escaping(self) -> None:
        self.assertEqual(
            self.render("  عنوان <&>\n  next\n\n  last"),
            "<pre><code>عنوان &lt;&amp;&gt;\nnext\n\nlast\n</code></pre>",
        )
        for content in [
            "  one\n\n\n\n  two\n\n  three",
            "  one\ndedented\n  two",
            "  before\n\u001f\n  after\u0085",
            "  @**King Hamlet** **bold** [link](https://zulip.com)",
            "* item\n\n    nested code\n\n* next",
            ">   code\n>   next",
            "  \ud800\n\n  fallback",
        ]:
            self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_code_sibling_shapes_and_remainders(self) -> None:
        for content in ["  title\nnormal\n  later", "  \u001f\n  ", "  \ud800\nnormal"]:
            for previous in [None, "", "earlier\n", "\ud800"]:
                for shape in ["none", "empty_pre", "pre_span", "pre_code", "paragraph"]:
                    results = []
                    for processor in [PythonCodeBlockProcessor, CodeBlockProcessor]:
                        engine = Markdown(tab_length=2)
                        parent = Element("div")
                        if shape == "paragraph":
                            SubElement(parent, "p").text = previous
                        elif shape != "none":
                            pre = SubElement(parent, "pre")
                            if shape != "empty_pre":
                                SubElement(
                                    pre, "code" if shape == "pre_code" else "span"
                                ).text = previous
                        blocks = [content, "remaining"]
                        processor(engine.parser).run(parent, blocks)
                        results.append((tostring(parent), blocks))
                    self.assertEqual(results[0], results[1])

    def test_empty_blocks_preserve_only_code_sibling_text(self) -> None:
        for content in ["", "\n", "\n\n  next", "\n\ud800"]:
            for previous in [None, "", "earlier\n", "\ud800"]:
                for shape in ["none", "empty_pre", "pre_span", "pre_code", "paragraph"]:
                    results = []
                    for processor in [PythonEmptyBlockProcessor, EmptyBlockProcessor]:
                        engine = Markdown(tab_length=2)
                        parent = Element("div")
                        if shape == "paragraph":
                            SubElement(parent, "p").text = previous
                        elif shape != "none":
                            pre = SubElement(parent, "pre")
                            if shape != "empty_pre":
                                SubElement(
                                    pre, "code" if shape == "pre_code" else "span"
                                ).text = previous
                        blocks = [content, "remaining"]
                        processor(engine.parser).run(parent, blocks)
                        results.append((tostring(parent), blocks))
                    self.assertEqual(results[0], results[1])

    def test_configured_indentation(self) -> None:
        for width in [-2, -1, 0, 1, 2, 4, 8]:
            results = []
            for processor in [PythonCodeBlockProcessor, CodeBlockProcessor]:
                engine = Markdown()
                engine.tab_length = width
                parent = Element("div")
                blocks = ["    title\n \u001f\n    next\nnormal"]
                processor(engine.parser).run(parent, blocks)
                self.assertIsInstance(parent[0][0].text, AtomicString)
                results.append((tostring(parent), blocks))
            self.assertEqual(results[0], results[1])

    def test_generated_renderings(self) -> None:
        rng = random.Random(0)
        for _ in range(1000):
            content = "\n".join(
                " " * rng.randrange(8)
                + "".join(rng.choices("abcعنوان📝<>*&\u001f\u0085", k=rng.randrange(10)))
                for _ in range(rng.randrange(1, 10))
            )
            self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_rust_planners_are_used(self) -> None:
        from zerver.lib.markdown import code_blocks

        if code_blocks._rust_code_blocks is None:
            self.skipTest("Rust code block planners are disabled")
        with (
            mock.patch.object(PythonCodeBlockProcessor, "run", side_effect=AssertionError),
            mock.patch.object(PythonEmptyBlockProcessor, "run", side_effect=AssertionError),
        ):
            self.assertEqual(
                self.render("  first\n\n\n  second\nnormal"),
                "<pre><code>first\n\n\nsecond\n</code></pre>\n<p>normal</p>",
            )

    def test_custom_escape_stays_on_reference_code_block_path(self) -> None:
        with mock.patch("markdown.util.code_escape", return_value="custom-escaped") as escape:
            self.assertEqual(self.render("  stock <&>"), "<pre><code>custom-escaped\n</code></pre>")
        escape.assert_called_once_with("stock <&>")
