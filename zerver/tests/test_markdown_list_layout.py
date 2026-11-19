import random
from contextlib import nullcontext
from unittest import TestCase, mock
from xml.etree.ElementTree import Element, SubElement, tostring

from markdown import Markdown
from markdown.blockprocessors import OListProcessor as PythonOListProcessor
from markdown.util import AtomicString

from zerver.lib.markdown import ListIndentProcessor, OListProcessor, UListProcessor, list_layout


class MarkdownListLayoutTest(TestCase):
    def render(self, content: str, use_python: bool = False) -> str:
        engine = Markdown(extensions=["sane_lists"])
        engine.parser.blockprocessors.register(ListIndentProcessor(engine.parser), "indent", 90)
        engine.parser.blockprocessors.register(OListProcessor(engine.parser), "olist", 40)
        engine.parser.blockprocessors.register(UListProcessor(engine.parser), "ulist", 30)
        with (
            mock.patch("zerver.lib.markdown.list_layout._rust_list_layout", None)
            if use_python
            else nullcontext()
        ):
            return engine.convert(content)

    def test_nested_and_loose_lists_preserve_rendering(self) -> None:
        for content in [
            "۳. عنوان **پررنگ**\n۴. next",
            "001. first\n002. second",
            "* first\n\n* second\n\n  paragraph",
            "* first\n\n  * nested\n\n  * second",
            "* first\n  1. nested\n  2. next\n* last",
            "1. first\n  * nested\n    * deeper\n2. last",
            "* first\n\n  ## heading\n\n* last",
            "* first\n\n  > quote\n\n* last",
            "* * first\n  * second",
            "* <script>& @**all**\n* [link](https://zulip.com)",
            "* \ud800\n\n* fallback",
            "\U00011f50. fallback\n\U00011f51. next",
        ]:
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))
        self.assertIn('<ol start="۳">', self.render("۳. title"))
        self.assertIn('<ol start="001">', self.render("001. title"))

    def test_generated_lists(self) -> None:
        rng = random.Random(4)
        lines = [
            "* item",
            "- item",
            "+ item",
            "003. item",
            "۳. item",
            "",
            "plain text",
            "  * nested",
            "  1. nested",
            "    * deeper",
            "  paragraph",
            "  ## heading",
            "  > quote",
            "* \u001f\u0085text",
            "* `a|b`",
            "* **عنوان**",
            "* ",
        ]
        for _ in range(1500):
            content = "\n".join(rng.choices(lines, k=rng.randrange(1, 15)))
            self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_tree_state_and_remaining_blocks(self) -> None:
        for processor_type in [OListProcessor, UListProcessor]:
            marker = "003." if processor_type is OListProcessor else "*"
            for shape in ["none", "paragraph", "matching", "different", "empty", "parent"]:
                for text in [None, "", AtomicString("previous <&>"), "\ud800"]:
                    for tail in [None, "", "\u001f\t tail", "\u001f", "\ud800"]:
                        results = []
                        for use_python in [True, False]:
                            engine = Markdown()
                            processor = processor_type(engine.parser)
                            parent = Element(processor.TAG if shape == "parent" else "div")
                            if shape == "paragraph":
                                SubElement(parent, "p").text = text
                            elif shape in {"matching", "different", "empty"}:
                                tag = (
                                    processor.TAG
                                    if shape != "different"
                                    else ("ul" if processor.TAG == "ol" else "ol")
                                )
                                sibling = SubElement(parent, tag)
                                if shape != "empty":
                                    previous = SubElement(sibling, "li")
                                    previous.text = text
                                    SubElement(previous, "h2").tail = tail
                            blocks = [f"{marker} first\n  * nested\n{marker} last", "following"]
                            error = None
                            with (
                                mock.patch(
                                    "zerver.lib.markdown.list_layout._rust_list_layout", None
                                )
                                if use_python
                                else nullcontext()
                            ):
                                try:
                                    processor.run(parent, blocks)
                                except IndexError as exc:
                                    error = type(exc)
                            tree = [
                                (node.tag, node.attrib, node.text, type(node.text), node.tail)
                                for node in parent.iter()
                            ]
                            results.append(
                                (
                                    tostring(parent),
                                    tree,
                                    blocks,
                                    processor.STARTSWITH,
                                    list(processor.parser.state),
                                    error,
                                )
                            )
                        self.assertEqual(results[0], results[1])

    def test_rust_layout_is_used(self) -> None:
        if list_layout._rust_list_layout is None:
            self.skipTest("Rust list layout is disabled")
        with (
            mock.patch.object(PythonOListProcessor, "run", side_effect=AssertionError),
            mock.patch.object(OListProcessor, "get_items", side_effect=AssertionError),
            mock.patch.object(UListProcessor, "get_items", side_effect=AssertionError),
        ):
            for content in ["3. first\n4. last", "* first\n  * nested\n\n* last"]:
                self.assertIn("<li>", self.render(content))

    def test_parser_errors_preserve_tree_and_state(self) -> None:
        for processor_type in [OListProcessor, UListProcessor]:
            marker = "3." if processor_type is OListProcessor else "*"
            for continuing in [False, True]:
                for first in ["", "first"]:
                    results = []
                    for use_python in [True, False]:
                        engine = Markdown()
                        processor = processor_type(engine.parser)
                        parent = Element("div")
                        if continuing:
                            SubElement(SubElement(parent, processor.TAG), "li").text = "previous"
                        blocks = [f"{marker} {first}", "following"]
                        with (
                            mock.patch("zerver.lib.markdown.list_layout._rust_list_layout", None)
                            if use_python
                            else nullcontext()
                        ):
                            with (
                                mock.patch.object(
                                    processor.parser,
                                    "parseBlocks",
                                    side_effect=ValueError("parser"),
                                ) as parse,
                                self.assertRaisesRegex(ValueError, "parser"),
                            ):
                                processor.run(parent, blocks)
                            self.assertEqual(parse.call_args.args[1], [first])
                        results.append(
                            (
                                tostring(parent),
                                blocks,
                                list(processor.parser.state),
                                processor.STARTSWITH,
                            )
                        )
                    self.assertEqual(results[0], results[1])

    def test_fallback_precedes_mutation(self) -> None:
        if list_layout._rust_list_layout is None:
            self.skipTest("Rust list layout is disabled")
        for block, text, tail in [
            ("* \ud800", None, None),
            ("* first", "\ud800", None),
            ("* first", "previous", "\ud800"),
            ("plain", None, None),
            ("\U00011f50. first", None, None),
        ]:
            processor = UListProcessor(Markdown().parser)
            parent = Element("div")
            previous = SubElement(SubElement(parent, "ul"), "li")
            previous.text = text
            SubElement(previous, "span").tail = tail
            blocks = [block, "following"]
            before = tostring(parent)
            with mock.patch.object(PythonOListProcessor, "run", autospec=True) as fallback:
                processor.run(parent, blocks)
                fallback.assert_called_once_with(processor, parent, blocks)
            self.assertEqual(blocks, [block, "following"])
            self.assertEqual(tostring(parent), before)
            self.assertEqual(processor.STARTSWITH, "1")

    def test_custom_configuration_uses_python(self) -> None:
        if list_layout._rust_list_layout is None:
            self.skipTest("Rust list layout is disabled")
        for setting in ["indentation", "tag", "siblings", "unordered_start", "empty_blocks"]:
            processor = UListProcessor(Markdown().parser)
            if setting == "indentation":
                processor.tab_length = 4
            elif setting == "tag":
                processor.TAG = "custom"  # type: ignore[misc] # Processor settings are ClassVars in stubs.
            elif setting == "siblings":
                processor.SIBLING_TAGS = ["ol", "ul"]  # type: ignore[misc] # Processor settings are ClassVars in stubs.
            elif setting == "unordered_start":
                processor.LAZY_OL = False  # type: ignore[misc] # Processor settings are ClassVars in stubs.
            parent = Element("div")
            blocks = [] if setting == "empty_blocks" else ["* first"]
            with mock.patch.object(PythonOListProcessor, "run", autospec=True) as fallback:
                processor.run(parent, blocks)
                fallback.assert_called_once_with(processor, parent, blocks)
            self.assertFalse(list(parent))
