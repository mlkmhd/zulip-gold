import random
import re
from collections.abc import Callable, Iterator
from typing import cast
from unittest import TestCase, mock
from xml.etree.ElementTree import Element, SubElement

from markdown import Markdown
from markdown.inlinepatterns import InlineProcessor as InlinePattern
from markdown.inlinepatterns import Pattern
from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor
from markdown.util import AtomicString
from typing_extensions import override

from zerver.lib.markdown import inline_stash
from zerver.lib.markdown.inline_stash import InlineProcessor

ApplyPattern = Callable[[Pattern, str, int, int], tuple[str, bool, int]]


class MarkdownInlineApplyTest(TestCase):
    def apply(self, processor: PythonInlineProcessor) -> ApplyPattern:
        return processor._InlineProcessor__applyPattern  # type: ignore[attr-defined] # Incomplete upstream stubs.

    def snapshot(self, node: Element | str) -> object:
        if isinstance(node, str):
            return type(node), node
        return [
            (child.tag, child.text, type(child.text), child.tail, type(child.tail), child.attrib)
            for child in node.iter()
        ]

    def exercise(
        self,
        cls: type[PythonInlineProcessor],
        mode: str,
        data: str = "فارسی x x 😀",
        start_index: int = 0,
    ) -> tuple[object, ...]:
        processor = cls(Markdown())
        processor.stashed_nodes = {"0000": "existing"}
        processor.ancestors = ["div", "p"]
        calls: list[object] = []
        nodes: list[Element] = []

        class TestPattern(InlinePattern):
            ANCESTOR_EXCLUDES = ("SPAN", "P") if mode == "excluded" else ()

            @override
            def handleMatch(  # type: ignore[override] # Incomplete upstream stubs.
                self, match: re.Match[str], text: str
            ) -> tuple[Element | str | None, int | None, int | None]:
                calls.append(("match", match.span(), text, processor.ancestors[:]))
                start, end = match.span()
                if mode == "reject" or (mode == "reject_first" and len(calls) == 1):
                    return None, None, None
                if mode == "no_start":
                    return None, None, end
                if mode == "no_end":
                    return None, start, None
                if mode == "none":
                    return None, start, end
                if mode in ("string", "atomic_string"):
                    node = AtomicString("*literal*") if mode == "atomic_string" else "*literal*"
                    return node, start, end
                element = Element("STRONG", {"title": 'فارسی <&> "'})
                element.text = AtomicString("*atomic*") if mode == "atomic_node" else "root"
                element.tail = "root tail"
                SubElement(element, "EM").text = "child"
                SubElement(element, "code").tail = "child tail"
                nodes.append(element)
                if mode == "invalid_slice":
                    return element, cast(int, "bad"), end
                return element, start, end

            @override
            def type(self) -> str:
                calls.append(("type", processor.ancestors[:]))
                if mode == "type_error":
                    processor.stashed_nodes["changed"] = "before type error"
                    raise RuntimeError("pattern type failed")
                return super().type()

        def handle(text: str, index: int) -> str:
            calls.append(("inline", text, index, processor.ancestors[:]))
            processor.stashed_nodes["nested"] = "callback stash"
            if mode == "recursive_error" or (mode == "tail_error" and text == "child tail"):
                raise RuntimeError("nested processing failed")
            if mode == "live_children" and text == "root":
                nodes[0].remove(nodes[0][0])
                SubElement(nodes[0], "added").text = "new child"
            return AtomicString(text + "!")

        result: object = None
        error: object = None
        with mock.patch.object(processor, "_InlineProcessor__handleInline", handle):
            try:
                result = self.apply(processor)(TestPattern("x"), data, 2, start_index)
            except (OverflowError, RuntimeError, TypeError) as exc:
                error = type(exc), str(exc)
        return (
            result,
            error,
            calls,
            processor.ancestors,
            {key: self.snapshot(node) for key, node in processor.stashed_nodes.items()},
            [self.snapshot(node) for node in nodes],
        )

    def test_matches_tree_mutations_and_partial_errors(self) -> None:
        for mode in [
            "excluded",
            "reject",
            "reject_first",
            "no_start",
            "no_end",
            "none",
            "string",
            "atomic_string",
            "node",
            "atomic_node",
            "live_children",
            "recursive_error",
            "tail_error",
            "type_error",
            "invalid_slice",
        ]:
            for data in ["فارسی x x 😀", "no match", "\ud800 x \udfff"]:
                with self.subTest(mode=mode, data=data):
                    self.assertEqual(
                        self.exercise(PythonInlineProcessor, mode, data),
                        self.exercise(InlineProcessor, mode, data),
                    )
        result = self.exercise(InlineProcessor, "node")
        self.assertEqual(result[0], ("فارسی \x02klzzwxh:0002\x03 x 😀", True, 0))
        self.assertEqual(result[3], ["div", "p"])
        failed = self.exercise(InlineProcessor, "recursive_error")
        self.assertEqual(failed[1], (RuntimeError, "nested processing failed"))
        self.assertEqual(failed[3], ["div", "p", "strong"])

    def test_python_indices_and_string_subclasses(self) -> None:
        for index in [-(10**100), -3, 0, 3, 10**100, True]:
            for data in ["فارسی x x 😀", AtomicString("x *literal* x")]:
                self.assertEqual(
                    self.exercise(PythonInlineProcessor, "string", data, index),
                    self.exercise(InlineProcessor, "string", data, index),
                )

    def test_match_result_unpacking_errors_and_side_effects(self) -> None:
        def exercise(cls: type[PythonInlineProcessor], value: object) -> tuple[object, ...]:
            processor = cls(Markdown())
            processor.stashed_nodes = {}

            class InvalidPattern(InlinePattern):
                @override
                def handleMatch(  # type: ignore[override] # Incomplete upstream stubs.
                    self, match: re.Match[str], data: str
                ) -> tuple[Element | None, int | None, int | None]:
                    processor.stashed_nodes["before"] = "changed"
                    return cast(tuple[Element | None, int | None, int | None], value)

            try:
                self.apply(processor)(InvalidPattern("x"), "x", 0, 0)
            except (TypeError, ValueError) as exc:
                return type(exc), str(exc), processor.stashed_nodes
            self.fail("Invalid match result was accepted")

        for value in [None, (), (None,), (None, 0), (None, 0, 1, 2), 3]:
            self.assertEqual(
                exercise(PythonInlineProcessor, value), exercise(InlineProcessor, value)
            )

    def test_iterator_result_and_rejection_cursor(self) -> None:
        def exercise(cls: type[PythonInlineProcessor]) -> tuple[object, ...]:
            processor = cls(Markdown())
            processor.stashed_nodes = {}
            seen = []

            class IteratorPattern(InlinePattern):
                @override
                def handleMatch(  # type: ignore[override] # Incomplete upstream stubs.
                    self, match: re.Match[str], data: str
                ) -> tuple[Element | str | None, int | None, int | None]:
                    seen.append(match.span())

                    def fields() -> Iterator[object]:
                        yield "replacement"
                        yield None if match.start() == 0 else match.start()
                        yield match.end()

                    return cast(tuple[str, int | None, int | None], fields())

            result = self.apply(processor)(IteratorPattern("x"), "x x x", 0, 0)
            return result, seen, processor.stashed_nodes

        outcomes = [exercise(cls) for cls in [PythonInlineProcessor, InlineProcessor]]
        self.assertEqual(*outcomes)
        self.assertEqual(outcomes[0][1], [(0, 1), (2, 3)])
        self.assertEqual(outcomes[0][0], ("x \x02klzzwxh:0000\x03 x", True, 0))

    def test_legacy_patterns_use_reference(self) -> None:
        pattern = Pattern("x")
        processor = InlineProcessor(Markdown())
        with mock.patch.object(
            PythonInlineProcessor,
            "_InlineProcessor__applyPattern",
            return_value=("legacy", False, 0),
        ) as reference:
            self.assertEqual(self.apply(processor)(pattern, "x", 0, 0), ("legacy", False, 0))
        reference.assert_called_once_with(pattern, "x", 0, 0)

    def test_python_isinstance_semantics_for_pattern_results(self) -> None:
        node = mock.Mock(spec=str)
        self.assertIsInstance(node, str)
        for cls in [PythonInlineProcessor, InlineProcessor]:
            processor = cls(Markdown())
            processor.stashed_nodes = {}
            pattern = InlinePattern("x")
            with mock.patch.object(pattern, "handleMatch", return_value=(node, 0, 1)):
                self.assertEqual(
                    self.apply(processor)(pattern, "x", 0, 0),
                    ("\x02klzzwxh:0000\x03", True, 0),
                )
            self.assertIs(processor.stashed_nodes["0000"], node)

    def test_false_match_and_match_iterator_errors(self) -> None:
        def exercise(cls: type[PythonInlineProcessor], fail: bool) -> tuple[object, ...]:
            processor = cls(Markdown())
            processor.stashed_nodes = {}
            pattern = InlinePattern("x")

            def matches() -> Iterator[object]:
                processor.stashed_nodes["finditer"] = "changed"
                if fail:
                    raise RuntimeError("match iterator failed")
                yield False

            compiled = mock.Mock()
            compiled.finditer.return_value = matches()
            with (
                mock.patch.object(pattern, "getCompiledRegExp", return_value=compiled),
                mock.patch.object(pattern, "handleMatch", return_value=(None, 0, 1)),
            ):
                try:
                    result: object = self.apply(processor)(pattern, "x", 0, 0)
                except RuntimeError as exc:
                    result = type(exc), str(exc)
            return result, processor.stashed_nodes

        for fail in [False, True]:
            self.assertEqual(exercise(PythonInlineProcessor, fail), exercise(InlineProcessor, fail))

    def test_ancestor_exclusion_reads_live_ancestors(self) -> None:
        def exercise(cls: type[PythonInlineProcessor]) -> tuple[object, ...]:
            processor = cls(Markdown())
            processor.ancestors = []
            pattern = InlinePattern("x")

            class Exclusion:
                def lower(self) -> str:
                    processor.ancestors = ["changed"]
                    return "changed"

            with (
                mock.patch.object(pattern, "ANCESTOR_EXCLUDES", [Exclusion()]),
                mock.patch.object(pattern, "getCompiledRegExp", side_effect=AssertionError),
            ):
                return self.apply(processor)(pattern, "x", 0, 0), processor.ancestors

        self.assertEqual(exercise(PythonInlineProcessor), exercise(InlineProcessor))

    def test_nested_and_generated_rendering(self) -> None:
        rng = random.Random(429)
        pieces = ["**", "*", "`", "[", "](url)", "<b>", "&amp;", "فارسی", "😀", "\n", " "]
        samples = [
            "**bold *nested* tail** and `*atomic*`",
            "[**link**](https://example.com) ![image](image.png)",
            "<span>raw</span> &amp; \\*literal*\nفارسی 😀",
            "* list\n    * nested **bold**\n\n> quote *emphasis*",
            "before \ud800 *bold* after",
        ]
        samples.extend("".join(rng.choices(pieces, k=rng.randrange(30))) for _ in range(1000))
        for text in samples:
            outputs = []
            for cls in [PythonInlineProcessor, InlineProcessor]:
                md = Markdown(extensions=["nl2br"])
                md.treeprocessors.register(cls(md), "inline", 20)
                outputs.append(md.convert(text))
            with self.subTest(text=text):
                self.assertEqual(*outputs)

    def test_rust_application_is_used(self) -> None:
        if inline_stash._rust_inline_apply is None:
            self.skipTest("Rust inline match application is disabled")
        processor = InlineProcessor(Markdown())
        processor.stashed_nodes = {}
        pattern = InlinePattern("x")
        with (
            mock.patch.object(
                PythonInlineProcessor, "_InlineProcessor__applyPattern", side_effect=AssertionError
            ),
            mock.patch.object(pattern, "handleMatch", return_value=(None, 0, 1)),
        ):
            self.assertEqual(self.apply(processor)(pattern, "x", 0, 0), ("x", True, 1))
