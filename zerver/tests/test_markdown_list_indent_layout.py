import random
from contextlib import nullcontext
from unittest import TestCase, mock
from xml.etree.ElementTree import Comment, Element, SubElement, tostring

from markdown import Markdown
from markdown.blockprocessors import ListIndentProcessor as PythonListIndentProcessor
from markdown.util import AtomicString

from zerver.lib.markdown import (
    ListIndentProcessor,
    OListProcessor,
    UListProcessor,
    list_indent_layout,
)


class MarkdownListIndentLayoutTest(TestCase):
    def render(self, content: str, use_python: bool = False) -> str:
        engine = Markdown(extensions=["sane_lists"])
        engine.parser.blockprocessors.register(ListIndentProcessor(engine.parser), "indent", 90)
        engine.parser.blockprocessors.register(OListProcessor(engine.parser), "olist", 40)
        engine.parser.blockprocessors.register(UListProcessor(engine.parser), "ulist", 30)
        with (
            mock.patch("zerver.lib.markdown.list_indent_layout._rust_list_indent_layout", None)
            if use_python
            else nullcontext()
        ):
            return engine.convert(content)

    def test_nested_blocks_and_code(self) -> None:
        for content in [
            "* first\n\n  * nested\n\n  * second\n* last",
            "1. first\n\n  * nested\n    * deeper\n\n    paragraph\n\n2. next",
            "* first\n\n      code\n      next\n\n  paragraph",
            "* * first\n    * next\n\n  * last",
            "* # Heading\n\n  paragraph\n\n    * nested",
            "* first\n\n  > quote\n  > next",
            "* عنوان\n\n  پاراگراف **پررنگ**\n\n  * بعدی",
            "* first\n\n  a|b\n  :-|-:\n  ۱|۲",
            "* first\n\n  \ud800",
        ]:
            self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_generated_renderings(self) -> None:
        rng = random.Random(5)
        lines = [
            "* item",
            "003. item",
            "۳. item",
            "",
            "  * nested",
            "    * deeper",
            "  paragraph",
            "  ## heading",
            "  > quote",
            "      code",
            "  **عنوان**",
        ]
        for _ in range(1000):
            content = "\n".join(rng.choices(lines, k=rng.randrange(1, 15)))
            self.assertEqual(self.render(content), self.render(content, use_python=True))

    def run_processor(
        self,
        tags: list[str],
        texts: list[str | None],
        block: str,
        tight: bool,
        fails: bool,
        use_python: bool,
    ) -> tuple[object, ...]:
        processor = ListIndentProcessor(Markdown().parser)
        parent = Element(tags[0])
        parent.text = texts[0]
        child = parent
        for tag, text in zip(tags[1:], texts[1:], strict=True):
            SubElement(child, "p").text = "earlier"
            child = SubElement(child, tag)
            child.text = text
        if tight:
            processor.parser.state.set("list")
        calls: list[tuple[str, str, list[str], list[str]]] = []

        def parse_blocks(target: Element, blocks: list[str]) -> None:
            calls.append(("blocks", target.tag, blocks, list(processor.parser.state)))
            if fails:
                raise ValueError("parser")

        def parse_chunk(target: Element, text: str) -> None:
            calls.append(("chunk", target.tag, [text], list(processor.parser.state)))
            if fails:
                raise ValueError("parser")

        blocks = [block, "following"]
        error = None
        with (
            (
                mock.patch.object(list_indent_layout, "_rust_list_indent_layout", None)
                if use_python
                else nullcontext()
            ),
            mock.patch.object(processor.parser, "parseBlocks", side_effect=parse_blocks),
            mock.patch.object(processor.parser, "parseChunk", side_effect=parse_chunk),
        ):
            try:
                processor.run(parent, blocks)
            except ValueError as exc:
                error = str(exc)
        tree = [(node.tag, node.text, type(node.text), node.tail) for node in parent.iter()]
        return tostring(parent), tree, blocks, calls, list(processor.parser.state), error

    def test_tree_calls_errors_and_state(self) -> None:
        rng = random.Random(6)
        for _ in range(1200):
            tags = [
                rng.choice(["div", "li", "ul", "ol"]),
                *rng.choices(["li", "ul", "ol", "p", "blockquote"], k=rng.randrange(6)),
            ]
            texts = rng.choices([None, "", AtomicString("previous <&>"), "\ud800"], k=len(tags))
            block = (
                " " * rng.randrange(18)
                + rng.choice(["عنوان", "* item", "۳. item", "\ud800"])
                + "\n  next\n"
            )
            tight = rng.choice([False, True])
            fails = rng.choice([False, True])
            self.assertEqual(
                self.run_processor(tags, texts, block, tight, fails, use_python=True),
                self.run_processor(tags, texts, block, tight, fails, use_python=False),
            )

    def test_rust_layout_is_used(self) -> None:
        if list_indent_layout._rust_list_indent_layout is None:
            self.skipTest("Rust list indentation layout is disabled")
        with (
            mock.patch.object(PythonListIndentProcessor, "run", side_effect=AssertionError),
            mock.patch.object(ListIndentProcessor, "get_level", side_effect=AssertionError),
            mock.patch.object(ListIndentProcessor, "looseDetab", side_effect=AssertionError),
        ):
            self.assertIn("<li>nested</li>", self.render("* first\n\n  * nested\n\n  paragraph"))

    def test_fallback_precedes_mutation(self) -> None:
        if list_indent_layout._rust_list_indent_layout is None:
            self.skipTest("Rust list indentation layout is disabled")
        for setting in ["surrogate", "indentation", "items", "lists", "empty", "comment"]:
            processor = ListIndentProcessor(Markdown().parser)
            if setting == "indentation":
                processor.tab_length = 4
            elif setting == "items":
                processor.ITEM_TYPES = ["custom"]
            elif setting == "lists":
                processor.LIST_TYPES = ["custom"]
            parent = Element("li")
            if setting == "comment":
                parent.append(Comment("last child"))
            before_tree = tostring(parent)
            blocks = (
                []
                if setting == "empty"
                else ["  \ud800" if setting == "surrogate" else "  text", "following"]
            )
            before = list(blocks)
            with mock.patch.object(PythonListIndentProcessor, "run", autospec=True) as fallback:
                processor.run(parent, blocks)
                fallback.assert_called_once_with(processor, parent, blocks)
            self.assertEqual(tostring(parent), before_tree)
            self.assertEqual(list(processor.parser.state), [])
            self.assertEqual(blocks, before)
