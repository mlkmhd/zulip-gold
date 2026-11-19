import copy
import random
import re
from collections.abc import Callable
from typing import cast
from unittest import TestCase, mock
from xml.etree.ElementTree import Element, SubElement

from markdown import Markdown
from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor
from markdown.util import AtomicString

from zerver.lib.markdown import inline_stash
from zerver.lib.markdown.inline_stash import InlineProcessor

ProcessPlaceholders = Callable[[str | None, Element, bool], list[tuple[Element, list[str]]]]


class MarkdownInlineStashTest(TestCase):
    def process(self, processor: PythonInlineProcessor) -> ProcessPlaceholders:
        return processor._InlineProcessor__processPlaceholders  # type: ignore[attr-defined] # Incomplete upstream stubs.

    def snapshot(self, root: Element) -> list[tuple[object, ...]]:
        return [
            (node.tag, node.text, type(node.text), node.tail, type(node.tail), node.attrib)
            for node in root.iter()
        ]

    def compare(
        self,
        data: str | None,
        stash: dict[str, Element | str],
        is_text: bool = True,
        initial: str | None = None,
    ) -> None:
        outcomes = []
        for processor_class in [PythonInlineProcessor, InlineProcessor]:
            processor = processor_class(Markdown())
            processor.stashed_nodes = copy.deepcopy(stash)
            processor.ancestors = ["div", "p"]
            parent = Element("p")
            parent.text = initial
            parent.tail = initial
            result = []
            error = None
            try:
                result = self.process(processor)(data, parent, is_text)
            except (TypeError, AttributeError) as exc:
                error = (type(exc), str(exc))
            outcomes.append(
                (
                    self.snapshot(parent),
                    [(self.snapshot(node), ancestors) for node, ancestors in result],
                    [
                        self.snapshot(node) if isinstance(node, Element) else node
                        for node in processor.stashed_nodes.values()
                    ],
                    error,
                )
            )
        self.assertEqual(outcomes[0], outcomes[1])

    def test_text_identifiers_and_malformed_placeholders(self) -> None:
        stash: dict[str, Element | str] = {
            "0000": "*",
            "1": "فارسی",
            "0001": AtomicString("atomic"),
            "": "empty",
        }
        for data in [
            None,
            "",
            "plain فارسی",
            "\x02klzzwxh:0000\x03",
            "before \x02klzzwxh:1\x03 after \x02klzzwxh:0001\x03",
            "\x02klzzwxh:unknown\x03 before \x02klzzwxh:0000\x03",
            "\x02klzzwxh:unknown\x03 before \x02klzzwxh:2\x03",
            "\x02klzzwxh:\x03 \x02klzzwxh:۴۲\x03 \x02klzzwxh:0001",
            "\ud800\x02klzzwxh:0000\x03",
            AtomicString("plain"),
            AtomicString("\x02klzzwxh:0001\x03 tail"),
            AtomicString("\x02klzzwxh:unknown\x03"),
        ]:
            for is_text in [True, False]:
                for initial in [None, "", AtomicString("initial ")]:
                    self.compare(data, stash, is_text, initial)

    def test_recursive_nodes_tails_and_ancestor_copies(self) -> None:
        root = Element("em")
        root.text = "before \x02klzzwxh:0001\x03 after"
        root.tail = "tail \x02klzzwxh:0002\x03"
        SubElement(root, "span").text = "child \x02klzzwxh:0002\x03"
        strong = Element("strong", {"title": "unchanged"})
        strong.text = "bold"
        stash: dict[str, Element | str] = {"0000": root, "0001": strong, "0002": "text"}
        for is_text in [True, False]:
            self.compare("start \x02klzzwxh:0000\x03 end", stash, is_text)
        self.compare("\x02klzzwxh:0001\x03 and \x02klzzwxh:0001\x03", stash)

    def test_partial_error_mutations(self) -> None:
        invalid = Element("em")
        invalid.text = cast(str, 42)
        self.compare("before \x02klzzwxh:0000\x03 after", {"0000": invalid})
        self.compare("before \x02klzzwxh:0000\x03", {"0000": cast(Element, None)})

    def check_live_stash_callback(self, processor_class: type[PythonInlineProcessor]) -> None:
        processor = processor_class(Markdown())
        node = Element("em")
        node.text = "trigger"
        processor.stashed_nodes = {"0000": node, "0001": "old"}
        processor.ancestors = ["p"]
        parent = Element("p")

        def callback(parent_node: Element, child: Element, is_text: bool = True) -> None:
            processor.stashed_nodes["0001"] = "new"
            processor.ancestors.append("em")
            node.text = "processed"

        with mock.patch.object(processor, "_InlineProcessor__processElementText", callback):
            result = self.process(processor)(
                "before \x02klzzwxh:0000\x03 \x02klzzwxh:0001\x03", parent, True
            )
        self.assertEqual(parent.text, "before ")
        self.assertEqual(node.tail, " new")
        self.assertEqual(result, [(node, ["p", "em"])])
        processor.ancestors.append("later")
        self.assertEqual(result[0][1], ["p", "em"])

    def test_callbacks_observe_live_stash_and_tree(self) -> None:
        self.check_live_stash_callback(PythonInlineProcessor)
        self.check_live_stash_callback(InlineProcessor)

    def test_custom_search_and_string(self) -> None:
        processor = InlineProcessor(Markdown())
        processor.stashed_nodes = {"id": "custom"}
        processor.ancestors = []
        parent = Element("p")
        with mock.patch.object(
            processor, "_InlineProcessor__findPlaceholder", return_value=("id", 10)
        ):
            self.process(processor)("\x02klzzwxh:x", parent, True)
        self.assertEqual(parent.text, "custom")
        parent.text = None
        with (
            mock.patch.object(processor, "_InlineProcessor__placeholder_prefix", "@"),
            mock.patch.object(processor, "_InlineProcessor__placeholder_re", re.compile(r"@(id)")),
        ):
            self.process(processor)("@id", parent, True)
        self.assertEqual(parent.text, "custom")

        class CustomString(str):
            __slots__ = ()

        self.compare(CustomString("text"), {})

    def test_generated_strings(self) -> None:
        rng = random.Random(23)
        pieces = ["\x02klzzwxh:", "\x03", "0000", "1", "2", "فارسی", "۴۲", "\n", "\ud800"]
        for _ in range(1500):
            data = "".join(rng.choices(pieces, k=rng.randrange(15)))
            self.compare(data, {"0000": "text", "1": Element("br")})

    def test_rust_scanner_is_used(self) -> None:
        if inline_stash._rust_inline_stash is None:
            self.skipTest("Rust inline stash is disabled")
        processor = InlineProcessor(Markdown())
        processor.stashed_nodes = {"0000": "فارسی"}
        processor.ancestors = []
        parent = Element("p")
        with mock.patch.object(
            PythonInlineProcessor,
            "_InlineProcessor__processPlaceholders",
            side_effect=AssertionError,
        ):
            self.process(processor)("before \x02klzzwxh:0000\x03 after", parent, True)
        self.assertEqual(parent.text, "before فارسی after")
