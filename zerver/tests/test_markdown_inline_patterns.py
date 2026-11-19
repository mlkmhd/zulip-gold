import random
import re
from collections.abc import Callable
from typing import cast
from unittest import TestCase, mock
from xml.etree.ElementTree import Element

from markdown import Markdown
from markdown.inlinepatterns import InlineProcessor as Pattern
from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor
from markdown.util import AtomicString, Registry

from zerver.lib.markdown import inline_stash
from zerver.lib.markdown.inline_stash import InlineProcessor


class MarkdownInlinePatternsTest(TestCase):
    def handle(self, processor: PythonInlineProcessor) -> Callable[[str, int], str]:
        return processor._InlineProcessor__handleInline  # type: ignore[attr-defined] # Incomplete upstream stubs.

    def render(self, processor_class: type[PythonInlineProcessor], text: str) -> str:
        md = Markdown(extensions=["nl2br"])
        md.treeprocessors.register(processor_class(md), "inline", 20)
        return md.convert(text)

    def test_nested_patterns_and_generated_messages(self) -> None:
        samples = [
            "**bold *nested* tail** and `*atomic*`",
            "[**link**](https://example.com) ![image](image.png)",
            "<span>raw</span> &amp; \\*literal*\nفارسی 😀",
            "* list\n    * nested **bold**\n\n> quote *emphasis*",
            "before \ud800 *bold* after",
        ]
        rng = random.Random(71)
        pieces = ["**", "*", "`", "[", "](url)", "<b>", "&amp;", "فارسی", "😀", "\n", " "]
        samples.extend("".join(rng.choices(pieces, k=rng.randrange(30))) for _ in range(500))
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(
                    self.render(PythonInlineProcessor, text), self.render(InlineProcessor, text)
                )

    def test_registry_and_callback_are_live_but_count_is_fixed(self) -> None:
        def exercise(
            cls: type[PythonInlineProcessor],
        ) -> tuple[str, list[tuple[str, str, int, int]]]:
            processor = cls(Markdown())
            processor.inlinePatterns = cast(Registry[Pattern], ["first", "second"])
            calls: list[tuple[str, str, int, int]] = []

            def replacement(pattern: str, data: str, index: int, start: int) -> object:
                calls.append((pattern, data, index, start))
                return iter((data + "!", False, 0))

            def apply(pattern: str, data: str, index: int, start: int) -> object:
                calls.append((pattern, data, index, start))
                processor.inlinePatterns = cast(Registry[Pattern], ["changed", "next", "extra"])
                processor._InlineProcessor__applyPattern = replacement  # type: ignore[attr-defined] # Incomplete upstream stubs.
                return [AtomicString(data + "?"), True, 7]

            with mock.patch.object(processor, "_InlineProcessor__applyPattern", apply):
                result = self.handle(processor)("text", 0)
            return result, calls

        outcomes = [exercise(cls) for cls in [PythonInlineProcessor, InlineProcessor]]
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[1][0], "text?!!")
        self.assertEqual([call[0] for call in outcomes[1][1]], ["first", "changed", "next"])
        self.assertEqual(outcomes[1][1][1][3], 7)

    def test_atomic_input_and_unusual_indices(self) -> None:
        for cls in [PythonInlineProcessor, InlineProcessor]:
            processor = cls(Markdown())
            atomic = AtomicString("*untouched*")
            with mock.patch.object(
                processor, "_InlineProcessor__applyPattern", side_effect=AssertionError
            ):
                self.assertIs(self.handle(processor)(atomic, 0), atomic)
                self.assertEqual(self.handle(processor)("text", 10**100), "text")
        for index in [-1, True, 0, 2, 10**100]:
            results = []
            for cls in [PythonInlineProcessor, InlineProcessor]:
                processor = cls(Markdown())
                processor.stashed_nodes = {}
                results.append(self.handle(processor)("فارسی **bold**", index))
            self.assertEqual(*results)

    def test_callback_is_resolved_before_registry_lookup(self) -> None:
        for cls in [PythonInlineProcessor, InlineProcessor]:
            processor = cls(Markdown())
            original = mock.Mock(return_value=("original", False, 0))
            replacement = mock.Mock(return_value=("replacement", False, 0))

            class RegistryWithCallback:
                def __init__(self, processor: PythonInlineProcessor, callback: mock.Mock) -> None:
                    self.processor = processor
                    self.callback = callback

                def __len__(self) -> int:
                    return 1

                def __getitem__(self, index: int) -> str:
                    self.processor._InlineProcessor__applyPattern = self.callback  # type: ignore[attr-defined] # Incomplete upstream stubs.
                    return "pattern"

            with (
                mock.patch.object(
                    processor, "inlinePatterns", RegistryWithCallback(processor, replacement)
                ),
                mock.patch.object(processor, "_InlineProcessor__applyPattern", original),
            ):
                self.assertEqual(self.handle(processor)("text", 0), "original")
            original.assert_called_once_with("pattern", "text", 0, 0)
            replacement.assert_not_called()

    def test_callback_errors_and_partial_state(self) -> None:
        def exercise(cls: type[PythonInlineProcessor], value: object) -> tuple[object, ...]:
            processor = cls(Markdown())
            calls = []

            def apply(*args: object) -> object:
                calls.append(args[1:])
                processor.stashed_nodes = {"0000": "changed"}
                return value

            with mock.patch.object(processor, "_InlineProcessor__applyPattern", apply):
                try:
                    self.handle(processor)("text", 0)
                except (TypeError, ValueError) as exc:
                    return type(exc), str(exc), calls, processor.stashed_nodes
                else:
                    self.fail("Invalid callback result was accepted")

        for value in [(), (1,), (1, False, 0, 4), None]:
            self.assertEqual(
                exercise(PythonInlineProcessor, value), exercise(InlineProcessor, value)
            )

    def test_rejected_match_and_ancestor_exclusion(self) -> None:
        class RejectFirst(Pattern):
            def handleMatch(
                self, match: re.Match[str], data: str
            ) -> tuple[Element | None, int | None, int | None]:
                start, end = match.span()
                if start == 0:
                    return None, None, None
                node = Element("em")
                node.text = AtomicString("*literal*")
                return node, start, end

        outputs = []
        for cls in [PythonInlineProcessor, InlineProcessor]:
            md = Markdown()
            md.inlinePatterns.register(RejectFirst(r"(?<!\w)x(?!\w)"), "reject", 200)
            md.treeprocessors.register(cls(md), "inline", 20)
            outputs.append(md.convert("x x [a [b](url)](url)"))
        self.assertEqual(*outputs)
        self.assertIn("x <em>*literal*</em>", outputs[0])

    def test_rust_dispatch_is_used(self) -> None:
        if inline_stash._rust_inline_patterns is None:
            self.skipTest("Rust inline patterns are disabled")
        with mock.patch.object(
            PythonInlineProcessor, "_InlineProcessor__handleInline", side_effect=AssertionError
        ):
            self.assertEqual(
                self.render(InlineProcessor, "**bold**"), "<p><strong>bold</strong></p>"
            )
