import random
import re
import sys
from types import FrameType
from typing import cast
from unittest import TestCase, mock
from xml.etree.ElementTree import Element, SubElement

from markdown import Markdown
from markdown.inlinepatterns import Pattern
from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor
from markdown.util import AtomicString

from zerver.lib.markdown import inline_stash
from zerver.lib.markdown.inline_stash import InlineProcessor


class MarkdownInlineEngineTest(TestCase):
    def render(self, cls: type[PythonInlineProcessor], content: str) -> str:
        md = Markdown(extensions=["nl2br"])
        md.treeprocessors.register(cls(md), "inline", 20)
        return md.convert(content)

    def test_complete_nested_rendering(self) -> None:
        samples = [
            "**bold *nested* tail** and `*atomic*`",
            "[**link**](https://example.com) ![image](image.png)",
            "<span>raw</span> &amp; \\*literal*\nفارسی 😀",
            "* list\n    * nested **bold**\n\n> quote *emphasis*",
            "before \ud800 *bold* after",
            "\x02klzzwxh:bad \x02klzzwxh:0000\x03",
        ]
        rng = random.Random(731)
        pieces = ["**", "*", "`", "[", "](url)", "<b>", "&amp;", "فارسی", "😀", "\n", " "]
        samples.extend("".join(rng.choices(pieces, k=rng.randrange(60))) for _ in range(1500))
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(
                    self.render(PythonInlineProcessor, text), self.render(InlineProcessor, text)
                )

    def snapshot(self, root: Element) -> list[tuple[object, ...]]:
        return [
            (node.tag, node.text, type(node.text), node.tail, type(node.tail), node.attrib)
            for node in root.iter()
        ]

    def exercise(self, cls: type[PythonInlineProcessor], mode: str) -> tuple[object, ...]:
        root = Element("div")
        SubElement(root, "p").text = "trigger"
        processor = cls(Markdown())
        node = Element("em")
        node.text = "before \x02klzzwxh:0001\x03 after"
        node.tail = AtomicString(" tail \x02klzzwxh:0002\x03")
        SubElement(node, "span").text = "child \x02klzzwxh:0002\x03"
        nested = Element("strong")
        nested.text = "bold"
        calls: list[object] = []
        original_process = processor._InlineProcessor__processElementText  # type: ignore[attr-defined] # Incomplete upstream stubs.

        def handle(data: str, index: int = 0) -> str:
            calls.append(("inline", data, index, processor.ancestors[:]))
            if data != "trigger":
                return data
            processor.stashed_nodes = {"0000": node, "0001": nested, "0002": "old"}
            return "start \x02klzzwxh:0000\x03 end \x02klzzwxh:0002\x03"

        def process(parent: Element, child: Element, is_text: bool = True) -> None:
            calls.append(("element", parent.tag, child.tag, is_text, child.text, child.tail))
            processor.stashed_nodes["0002"] = "changed"
            processor.ancestors = ["replacement"]
            if mode == "error":
                child.tail = "before error"
                raise RuntimeError("element callback failed")
            if mode == "invalid":
                child.text = cast(str, 42)
            if mode == "replace_callback":
                processor._InlineProcessor__processElementText = original_process  # type: ignore[attr-defined] # Incomplete upstream stubs.
            original_process(parent, child, is_text)

        error = None
        with (
            mock.patch.object(processor, "_InlineProcessor__handleInline", handle),
            mock.patch.object(processor, "_InlineProcessor__processElementText", process),
        ):
            try:
                processor.run(root)
            except (AttributeError, RuntimeError, TypeError) as exc:
                error = type(exc), str(exc)
        return (
            self.snapshot(root),
            self.snapshot(node),
            self.snapshot(nested),
            processor.ancestors,
            calls,
            error,
        )

    def test_live_recursive_callbacks_and_error_state(self) -> None:
        for mode in ["normal", "error", "invalid", "replace_callback"]:
            with self.subTest(mode=mode):
                self.assertEqual(
                    self.exercise(PythonInlineProcessor, mode), self.exercise(InlineProcessor, mode)
                )

    def test_custom_placeholder_search_and_string_subclass(self) -> None:
        class CustomString(str):
            __slots__ = ()

        for cls in [PythonInlineProcessor, InlineProcessor]:
            processor = cls(Markdown())
            root = Element("div")
            SubElement(root, "p").text = CustomString("@custom")

            def handle(data: str, processor: PythonInlineProcessor = processor) -> str:
                processor.stashed_nodes["id"] = AtomicString("فارسی")
                return data

            with (
                mock.patch.object(processor, "_InlineProcessor__handleInline", handle),
                mock.patch.object(processor, "_InlineProcessor__placeholder_prefix", "@"),
                mock.patch.object(
                    processor, "_InlineProcessor__placeholder_re", re.compile("@(custom)")
                ),
                mock.patch.object(
                    processor, "_InlineProcessor__findPlaceholder", return_value=("id", 7)
                ),
            ):
                processor.run(root)
            self.assertEqual(root[0].text, "فارسی")
            self.assertIsInstance(root[0].text, AtomicString)

    def test_live_placeholder_creation(self) -> None:
        outcomes = []
        for cls in [PythonInlineProcessor, InlineProcessor]:
            processor = cls(Markdown())
            md = processor.md
            md.treeprocessors.register(processor, "inline", 20)
            calls: list[str] = []

            def placeholder(
                kind: str,
                processor: PythonInlineProcessor = processor,
                calls: list[str] = calls,
            ) -> tuple[str, str]:
                calls.append(kind)
                identifier = f"custom-{len(processor.stashed_nodes)}"
                return "@" + identifier, identifier

            with mock.patch.object(processor, "_InlineProcessor__makePlaceholder", placeholder):
                outcomes.append((md.convert("**bold** *em* `code`"), calls))
        self.assertEqual(*outcomes)
        self.assertIn("@custom-", outcomes[0][0])

    def test_legacy_patterns_still_use_reference(self) -> None:
        outcomes = []
        for cls in [PythonInlineProcessor, InlineProcessor]:
            md = Markdown()
            md.inlinePatterns.register(Pattern("x"), "legacy", 200)
            md.treeprocessors.register(cls(md), "inline", 20)
            outcomes.append(md.convert("x **bold**"))
        self.assertEqual(*outcomes)

    def test_placeholder_state_and_error_matrix(self) -> None:
        def exercise(
            cls: type[PythonInlineProcessor], data: str | None, invalid: bool
        ) -> tuple[object, ...]:
            processor = cls(Markdown())
            root = Element("div")
            SubElement(root, "p").text = "trigger"
            node = Element("em")
            node.text = "nested \x02klzzwxh:0001\x03"
            SubElement(node, "span").tail = "tail \x02klzzwxh:0002\x03"

            def handle(text: str) -> str:
                if text != "trigger":
                    return text
                processor.stashed_nodes = {
                    "0000": node,
                    "0001": AtomicString("فارسی"),
                    "0002": "text",
                    "0003": cast(Element, None) if invalid else Element("br"),
                }
                return cast(str, data)

            error = None
            with mock.patch.object(processor, "_InlineProcessor__handleInline", handle):
                try:
                    processor.run(root)
                except (AttributeError, TypeError, ValueError) as exc:
                    error = type(exc), str(exc)
            return (
                self.snapshot(root),
                self.snapshot(node),
                processor.ancestors,
                sorted(
                    [
                        (self.snapshot(child), self.snapshot(parent))
                        for child, parent in processor.parent_map.items()
                    ],
                    key=repr,
                ),
                error,
            )

        rng = random.Random(915)
        pieces = [
            "\x02klzzwxh:",
            "\x03",
            "0000",
            "0001",
            "0002",
            "0003",
            "فارسی",
            "۴۲",
            "\n",
            "\ud800",
        ]
        samples: list[str | None] = [
            None,
            "",
            "\x02klzzwxh:0000\x03 and \x02klzzwxh:0000\x03",
            "before \x02klzzwxh:0003\x03 after",
            "\x02klzzwxh:bad \x02klzzwxh:0000\x03 end",
            AtomicString("plain فارسی"),
            AtomicString("\x02klzzwxh:0000\x03 tail"),
        ]
        samples.extend("".join(rng.choices(pieces, k=rng.randrange(25))) for _ in range(1500))
        for data in samples:
            for invalid in [False, True]:
                with self.subTest(data=data, invalid=invalid):
                    self.assertEqual(
                        exercise(PythonInlineProcessor, data, invalid),
                        exercise(InlineProcessor, data, invalid),
                    )

    def test_deep_placeholder_chains(self) -> None:
        def exercise(cls: type[PythonInlineProcessor], count: int) -> tuple[object, ...]:
            processor = cls(Markdown())
            root = Element("div")
            SubElement(root, "p").text = "trigger"

            def handle(data: str) -> str:
                if data != "trigger":
                    return data
                for index in range(count):
                    node = Element("span")
                    node.text = f"\x02klzzwxh:{index + 1:04d}\x03" if index + 1 < count else "end"
                    processor.stashed_nodes[f"{index:04d}"] = node
                return "\x02klzzwxh:0000\x03"

            with mock.patch.object(processor, "_InlineProcessor__handleInline", handle):
                try:
                    processor.run(root)
                except RecursionError:
                    return (RecursionError,)
            return tuple(node.tag for node in root.iter())

        for count in [40, 2500]:
            self.assertEqual(
                exercise(PythonInlineProcessor, count), exercise(InlineProcessor, count)
            )

    def test_default_methods_execute_without_python_dispatch(self) -> None:
        if inline_stash._rust_inline_engine is None:
            self.skipTest("Rust inline engine is disabled")
        codes = {callback.__code__ for callback in inline_stash._inline_engine_callbacks.values()}
        calls: list[str] = []

        def profile(frame: FrameType, event: str, arg: object) -> None:
            if event == "call" and frame.f_code in codes:
                calls.append(frame.f_code.co_name)

        previous_profile = sys.getprofile()
        try:
            sys.setprofile(profile)
            actual = self.render(InlineProcessor, "**bold *nested*** and `code` فارسی")
        finally:
            sys.setprofile(previous_profile)
        self.assertEqual(
            actual, "<p><strong>bold <em>nested</em></strong> and <code>code</code> فارسی</p>"
        )
        self.assertEqual(calls, [])
