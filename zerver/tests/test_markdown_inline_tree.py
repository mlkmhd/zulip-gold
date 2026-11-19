import copy
import random
import re
from typing import NamedTuple, cast
from unittest import TestCase, mock
from xml.etree.ElementTree import Comment, Element, SubElement

from markdown import Markdown
from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor
from markdown.util import AtomicString

from zerver.lib.markdown import inline_stash
from zerver.lib.markdown.inline_stash import InlineProcessor

TreeSnapshot = list[tuple[object, ...]]


class TreeResult(NamedTuple):
    tree: TreeSnapshot
    error: object
    ancestors: list[str]
    parents: list[tuple[TreeSnapshot, TreeSnapshot]]
    stash: dict[str, object]
    calls: list[object]
    original_ancestors: list[str] | None


class MarkdownInlineTreeTest(TestCase):
    def snapshot(self, root: Element) -> TreeSnapshot:
        return [
            (
                node.tag,
                node.text,
                type(node.text),
                node.tail,
                type(node.tail),
                node.attrib,
                len(node),
            )
            for node in root.iter()
        ]

    def exercise(
        self,
        cls: type[PythonInlineProcessor],
        root: Element,
        mode: str = "normal",
        ancestors: list[str] | None = None,
    ) -> TreeResult:
        root = copy.deepcopy(root)
        processor = cls(Markdown())
        processor.stashed_nodes = {"old": "discarded"}
        processor.parent_map = {}
        processor.ancestors = ["old"]
        calls: list[object] = []
        original_handle = processor._InlineProcessor__handleInline  # type: ignore[attr-defined] # Incomplete upstream stubs.
        original_placeholders = processor._InlineProcessor__processPlaceholders  # type: ignore[attr-defined] # Incomplete upstream stubs.

        def handle(data: str, index: int = 0) -> str:
            calls.append(("inline", data, index, processor.ancestors[:]))
            if mode == "handle_error" or (mode == "tail_handle_error" and data == "remove tail"):
                raise RuntimeError("inline callback failed")
            if mode == "replace_map":
                processor.parent_map = {}
            if mode == "replace_ancestors":
                processor.ancestors = ["replacement"]
            if mode == "append_child" and data == "trigger":
                SubElement(root, "p").text = "late **child**"
            if mode == "remove_child" and data == "remove tail":
                root.remove(root[0])
            return cast(str, original_handle(data, index))

        def placeholders(data: str | None, parent: Element, is_text: bool = True) -> object:
            calls.append(("placeholders", data, parent.tag, is_text, parent.text, parent.tail))
            if mode == "placeholder_error":
                raise RuntimeError("placeholder callback failed")
            if mode == "short_pair":
                return [(Element("span"),)]
            if mode == "invalid_pair":
                return [(Element("span"), [], "extra")]
            return original_placeholders(data, parent, is_text)

        error = None
        with (
            mock.patch.object(processor, "_InlineProcessor__handleInline", handle),
            mock.patch.object(processor, "_InlineProcessor__processPlaceholders", placeholders),
        ):
            try:
                self.assertIs(processor.run(root, ancestors), root)
            except (AttributeError, IndexError, RuntimeError, TypeError, ValueError) as exc:
                error = type(exc), re.sub(r" at 0x[0-9a-f]+", " at <address>", str(exc))
        parents = sorted(
            [
                (self.snapshot(node), self.snapshot(parent))
                for node, parent in processor.parent_map.items()
            ],
            key=repr,
        )
        stash: dict[str, object] = {
            key: (type(node), node) if isinstance(node, str) else self.snapshot(node)
            for key, node in processor.stashed_nodes.items()
        }
        return TreeResult(
            self.snapshot(root), error, processor.ancestors, parents, stash, calls, ancestors
        )

    def compare(
        self, root: Element, mode: str = "normal", ancestors: list[str] | None = None
    ) -> None:
        expected = self.exercise(PythonInlineProcessor, root, mode, copy.deepcopy(ancestors))
        actual = self.exercise(InlineProcessor, root, mode, copy.deepcopy(ancestors))
        self.assertEqual(expected, actual)

    def test_nested_elements_tails_atomic_strings_and_ancestors(self) -> None:
        root = Element("div")
        root.text = "**root is untouched**"
        paragraph = SubElement(root, "p")
        paragraph.text = "فارسی **bold *nested*** 😀"
        link = SubElement(paragraph, "a", {"href": "https://example.com"})
        link.text = "[excluded](url) *emphasis*"
        link.tail = " tail **strong**"
        code = SubElement(paragraph, "code")
        code.text = AtomicString("*literal*")
        code.tail = AtomicString("**atomic tail**")
        SubElement(root, "p").text = "before \ud800 *after*"
        self.compare(root)
        self.compare(root, ancestors=["outer", "a"])
        result = self.exercise(InlineProcessor, root, ancestors=["outer"])
        self.assertEqual(result.tree[0][1], "**root is untouched**")
        self.assertEqual(result.original_ancestors, ["outer"])
        self.assertNotIn("old", result.stash)

    def test_generated_trees(self) -> None:
        rng = random.Random(438)
        texts = [
            None,
            "",
            "plain",
            "فارسی **bold**",
            "*nested `code`*",
            "[link](url)",
            "\ud800",
            AtomicString("**atomic**"),
        ]
        for index in range(300):
            root = Element("div")
            nodes = [root]
            for _ in range(rng.randrange(15)):
                node = SubElement(
                    rng.choice(nodes), rng.choice(["p", "span", "a", "code", "strong"])
                )
                node.text = rng.choice(texts)
                node.tail = rng.choice(texts)
                nodes.append(node)
            with self.subTest(index=index):
                self.compare(root, ancestors=rng.choice([None, [], ["div"], ["a"]]))

    def test_live_child_iteration_and_partial_callback_errors(self) -> None:
        for mode in [
            "append_child",
            "remove_child",
            "handle_error",
            "tail_handle_error",
            "replace_map",
            "replace_ancestors",
            "short_pair",
            "placeholder_error",
            "invalid_pair",
        ]:
            root = Element("div")
            paragraph = SubElement(root, "p")
            paragraph.text = "trigger"
            paragraph.tail = "remove tail"
            with self.subTest(mode=mode):
                self.compare(root, mode)
        root = Element("div")
        SubElement(root, "p").text = "trigger"
        result = self.exercise(InlineProcessor, root, "handle_error")
        self.assertEqual(result.tree[1][1], None)
        self.assertEqual(result.error, (RuntimeError, "inline callback failed"))
        self.assertEqual(result.ancestors, ["div", "p"])

    def test_nonstring_tags_and_placeholder_errors(self) -> None:
        for tag in [None, 3, b"p", Comment]:
            root = Element("div")
            child = SubElement(root, cast(str, tag))
            child.text = "text"
            child.tail = "tail"
            self.compare(root)
        root = Element("div")
        child = SubElement(root, "p")
        child.text = "\x02klzzwxh:9999\x03 *valid*"
        child.tail = "\x02klzzwxh:0000\x03"
        self.compare(root)

    def test_initialization_errors_preserve_previous_parent_map(self) -> None:
        for ancestors in [None, 3]:
            results = []
            for cls in [PythonInlineProcessor, InlineProcessor]:
                processor = cls(Markdown())
                processor.parent_map = cast(dict[Element, Element], {"old": "map"})
                processor.ancestors = ["old"]
                processor.stashed_nodes = {"old": "stash"}
                with self.assertRaises((AttributeError, TypeError)) as caught:
                    processor.run(cast(Element, None), cast(list[str] | None, ancestors))
                results.append(
                    (
                        type(caught.exception),
                        str(caught.exception),
                        processor.parent_map,
                        processor.ancestors,
                        processor.stashed_nodes,
                    )
                )
            self.assertEqual(*results)
            self.assertEqual(results[0][-1], {})
            self.assertEqual(results[0][2], {"old": "map"})

    def test_placeholder_callback_is_selected_before_inline_handling(self) -> None:
        def exercise(cls: type[PythonInlineProcessor]) -> tuple[object, ...]:
            root = Element("div")
            SubElement(root, "p").text = "text"
            processor = cls(Markdown())
            calls = []

            def replacement(*args: object) -> list[object]:
                calls.append("replacement")
                return []

            def original(*args: object) -> list[object]:
                calls.append("original")
                return []

            def handle(text: str) -> str:
                processor._InlineProcessor__processPlaceholders = replacement  # type: ignore[attr-defined] # Incomplete upstream stubs.
                return text

            with (
                mock.patch.object(processor, "_InlineProcessor__handleInline", handle),
                mock.patch.object(processor, "_InlineProcessor__processPlaceholders", original),
            ):
                processor.run(root)
            return calls, self.snapshot(root)

        self.assertEqual(exercise(PythonInlineProcessor), exercise(InlineProcessor))
        self.assertEqual(exercise(InlineProcessor)[0], ["original"])

    def test_rust_tree_runner_is_used(self) -> None:
        if inline_stash._rust_inline_tree is None:
            self.skipTest("Rust inline tree traversal is disabled")
        root = Element("div")
        SubElement(root, "p").text = "**bold**"
        processor = InlineProcessor(Markdown())
        with mock.patch.object(PythonInlineProcessor, "run", side_effect=AssertionError):
            self.assertIs(processor.run(root), root)
        self.assertEqual(root[0][0].tag, "strong")
        self.assertEqual(root[0][0].text, "bold")
