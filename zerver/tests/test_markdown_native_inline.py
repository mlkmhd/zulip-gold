import random
import re
import sys
from collections.abc import Callable
from types import FrameType
from typing import cast
from unittest import TestCase, mock
from xml.etree.ElementTree import Element, SubElement

from markdown import inlinepatterns, util
from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor
from markdown.util import AtomicString

from zerver.lib.markdown import (
    MessageRenderingResult,
    ZulipMarkdown,
    do_convert,
    inline_stash,
    native_inline,
)
from zerver.lib.markdown.inline_stash import InlineProcessor


class MarkdownNativeInlineTest(TestCase):
    def markdown(self) -> ZulipMarkdown:
        md = ZulipMarkdown(linkifiers=[], linkifiers_key=0, email_gateway=False)
        md.zulip_db_data = None
        return md

    def snapshot(self, processor: PythonInlineProcessor, root: Element) -> tuple[object, ...]:
        nodes = [root]
        indices = {root: 0}

        def index(node: Element) -> int:
            if node not in indices:
                indices[node] = len(nodes)
                nodes.append(node)
            return indices[node]

        stash = [
            (key, (value, type(value)) if isinstance(value, str) else index(value))
            for key, value in processor.stashed_nodes.items()
        ]
        parents = [(index(node), index(parent)) for node, parent in processor.parent_map.items()]
        tree = [
            (
                node.tag,
                node.text,
                type(node.text),
                node.tail,
                type(node.tail),
                node.attrib,
                [index(child) for child in node],
            )
            for node in nodes
        ]
        return tree, stash, parents, processor.ancestors

    def compare(self, build: Callable[[], Element], *, accepted: bool = True) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        expected = PythonInlineProcessor(self.markdown())
        root = build()
        expected.md.treeprocessors.register(expected, "inline", 25)
        expected.run(root, ["outer"])
        reference = self.snapshot(expected, root)
        processor = InlineProcessor(self.markdown())
        processor.md.treeprocessors.register(processor, "inline", 25)
        root = build()
        before = [(node, node.text, node.tail, list(node)) for node in root.iter()]
        result = native_inline.try_run(
            processor, root, ["outer"], native.plan_markdown_native_inline
        )
        self.assertEqual(result, accepted)
        if not result:
            self.assertEqual(
                before, [(node, node.text, node.tail, list(node)) for node in root.iter()]
            )
            PythonInlineProcessor.run(processor, root, ["outer"])
        self.assertEqual(reference, self.snapshot(processor, root))

    def test_native_formatting_and_complete_state(self) -> None:
        samples = [
            "plain فارسی 123",
            "**bold *nested* tail** and `*atomic*`",
            "***~~nested~~*** and ~~**bold**~~",
            "` <&> \n ` before\n**after**",
            "\\\\`x` \\``y`",
            "* _ __ ___ ** ***\ntrailing *",
            "**a**b** ****x**** *****",
            "\x02klzzwxh:bad \x02klzzwxh:0000\x03 **bold**",
        ]
        rng = random.Random(961)
        pieces = ["***", "**", "*", "~~", "`", "\\", "فارسی", "abc", "123", "\n", " ", "_", "^"]
        samples.extend("".join(rng.choices(pieces, k=rng.randrange(40))) for _ in range(600))
        for text in samples:
            with self.subTest(text=text):

                def build(text: str = text) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = text
                    return root

                self.compare(build)

    def test_shared_nodes_atomic_text_and_live_tail_iteration(self) -> None:
        def build() -> Element:
            root = Element("div", {"unchanged": "<&>"})
            root.text = "**root stays unchanged**"
            paragraph = SubElement(root, "p")
            paragraph.text = "***nested***"
            shared = SubElement(paragraph, "span", {"title": "shared"})
            shared.text = AtomicString("*atomic* \x02klzzwxh:0000\x03")
            shared.tail = "**tail *nested* tail**\nend"
            paragraph.append(shared)
            other = SubElement(root, "section")
            other.append(shared)
            SubElement(other, "code").text = AtomicString("**code**")
            return root

        self.compare(build)

    def test_unicode_whitespace_around_emphasis(self) -> None:
        for separator in [
            "\t",
            "\n",
            "\u001c",
            "\u0085",
            "\u00a0",
            "\u1680",
            "\u200b",
            "\u2028",
            "\u2029",
            "\u3000",
        ]:
            with self.subTest(separator=repr(separator)):

                def build(separator: str = separator) -> Element:
                    root = Element("div")
                    SubElement(
                        root, "p"
                    ).text = f"*{separator}* {separator}**bold**{separator}~~strike~~"
                    return root

                self.compare(build)

    def test_original_elements_keep_identity_and_attributes(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        root = Element("div")
        paragraph = SubElement(root, "p", {"key": "value"})
        paragraph.text = "**bold**"
        attributes = paragraph.attrib
        processor = InlineProcessor(self.markdown())
        self.assertTrue(
            native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
        )
        self.assertIs(root[0], paragraph)
        self.assertIs(paragraph.attrib, attributes)
        self.assertEqual(paragraph.attrib, {"key": "value"})
        self.assertIs(paragraph[0], processor.stashed_nodes["0000"])
        self.assertIs(processor.parent_map[paragraph[0]], paragraph)

    def test_supported_ancestor_exclusions(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        outcomes = []
        for cls in (PythonInlineProcessor, InlineProcessor):
            md = self.markdown()
            md.inlinePatterns["strong"].__dict__["ANCESTOR_EXCLUDES"] = ("P",)
            root = Element("div")
            SubElement(root, "p").text = "**bold**"
            processor = cls(md)
            if isinstance(processor, InlineProcessor):
                self.assertTrue(
                    native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
                )
            else:
                processor.run(root)
            outcomes.append(self.snapshot(processor, root))
        self.assertEqual(*outcomes)

    def test_unsupported_syntax_delegates_before_mutation(self) -> None:
        for text in [
            "**earlier** [link](https://example.com)",
            "**earlier** :smile:",
            "**earlier** 😀",
            "**earlier** &copy;",
            "**earlier** $$x$$",
        ]:
            with self.subTest(text=text):

                def build(text: str = text) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = text
                    return root

                self.compare(build, accepted=False)

    def test_custom_callbacks_and_registry_delegate(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        root = Element("div")
        SubElement(root, "p").text = "**text**"
        processor = InlineProcessor(self.markdown())
        callback = mock.Mock(side_effect=AssertionError("custom callback ran during preflight"))
        for name in ("_InlineProcessor__handleInline", "_InlineProcessor__findPlaceholder"):
            with self.subTest(name=name), mock.patch.object(processor, name, callback):
                self.assertFalse(
                    native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
                )
        pattern = processor.inlinePatterns["strong"]
        with mock.patch.object(pattern, "handleMatch", callback):
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
        cast(util.Registry[inlinepatterns.Pattern], processor.inlinePatterns).register(
            inlinepatterns.Pattern("text"), "custom", 200
        )
        self.assertFalse(
            native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
        )
        callback.assert_not_called()
        self.assertEqual(root[0].text, "**text**")
        self.assertFalse(hasattr(processor, "stashed_nodes"))

    def test_surrogate_and_custom_values_delegate(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")

        class CustomString(str):
            __slots__ = ()

        for value in ["before \ud800 **bold**", CustomString("**bold**"), cast(str, 42)]:
            with self.subTest(value=value):
                root = Element("div")
                SubElement(root, "p").text = value
                self.assertFalse(
                    native_inline.try_run(
                        InlineProcessor(self.markdown()),
                        root,
                        None,
                        native.plan_markdown_native_inline,
                    )
                )
                self.assertEqual(root[0].text, value)

    def test_depth_and_cycles_delegate_safely(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        root = Element("div")
        parent = root
        for _ in range(66):
            parent = SubElement(parent, "div")
        parent.text = "**deep**"
        processor = InlineProcessor(self.markdown())
        self.assertFalse(
            native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
        )
        self.assertEqual(parent.text, "**deep**")
        parent.append(root)
        self.assertFalse(
            native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
        )
        parent.remove(root)

    def test_registry_sorting_is_committed_only_on_acceptance(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        for text, expected in [
            ("**bold**", True),
            ("**bold** :smile:", False),
            (AtomicString("**atomic**"), True),
        ]:
            md = self.markdown()
            state = cast(native_inline.RegistryInternals, md.inlinePatterns)
            processor = InlineProcessor(md)
            root = Element("div")
            SubElement(root, "p").text = text
            before = list(state._priority)
            self.assertFalse(state._is_sorted)
            self.assertEqual(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline),
                expected,
            )
            if not expected or isinstance(text, AtomicString):
                self.assertEqual(state._priority, before)
                self.assertFalse(state._is_sorted)
            else:
                self.assertTrue(state._is_sorted)
                self.assertEqual(
                    state._priority,
                    sorted(before, key=lambda p: p.priority, reverse=True),
                )

    def test_invalid_pattern_contracts_and_sort_callback_delegate(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        root = Element("div")
        SubElement(root, "p").text = "**bold**"
        processor = InlineProcessor(self.markdown())
        callback = mock.Mock(side_effect=AssertionError("custom sorter called during preflight"))
        with mock.patch.object(processor.inlinePatterns, "_sort", callback):
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
            callback.assert_not_called()
        for name, attribute, value in [
            ("backtick", "tag", "custom"),
            ("backtick", "ESCAPED_BSLASH", "custom"),
            ("backtick", "compiled_re", object()),
            ("strong", "compiled_re", re.compile("custom")),
            ("strong", "tag", "custom"),
            ("strong", "ANCESTOR_EXCLUDES", {"p"}),
            ("strong", "ANCESTOR_EXCLUDES", ["\ud800"]),
            ("nl", "tag", "custom"),
        ]:
            with self.subTest(name=name, attribute=attribute):
                pattern = processor.inlinePatterns[name]
                with mock.patch.object(pattern, attribute, value):
                    self.assertFalse(
                        native_inline.try_run(
                            processor, root, None, native.plan_markdown_native_inline
                        )
                    )
        self.assertEqual(root[0].text, "**bold**")

    def test_full_rendering_result_and_fallback(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        for content in [
            "سلام **دنیا**\nمتن `code` و *تأکید*.",
            "## **heading**\n\n* ***item***\n* ~~item~~\n\n> **quote**",
            "**format** [link](https://example.com) 😀 :zulip: &copy;",
            "|text|code|\n|---|---|\n|**bold**|`<&>`|",
            "```python\nprint('فارسی')\n```\n\n**after**",
        ]:
            with self.subTest(content=content):
                with mock.patch.object(inline_stash, "_rust_native_inline", None):
                    expected: MessageRenderingResult = do_convert(content, no_previews=True)
                self.assertEqual(do_convert(content, no_previews=True), expected)

    def test_accepted_run_does_not_call_python_inline_handlers(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        root = Element("div")
        SubElement(root, "p").text = "***bold*** `code` ~~strike~~\nفارسی"
        processor = InlineProcessor(self.markdown())
        with mock.patch.object(
            inlinepatterns.SimpleTagPattern, "handleMatch", autospec=True
        ) as callback:
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
            callback.assert_not_called()
        from zerver.lib.markdown import _native_inline_handlers

        forbidden = {
            callback.__code__
            for callbacks in _native_inline_handlers.values()
            for callback in callbacks
        }
        forbidden.update(
            callback.__code__ for callback in inline_stash._inline_engine_callbacks.values()
        )
        calls = []

        def profile(frame: FrameType, event: str, arg: object) -> None:
            if event == "call" and frame.f_code in forbidden:
                calls.append(frame.f_code.co_name)

        previous = sys.getprofile()
        try:
            sys.setprofile(profile)
            self.assertTrue(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
        finally:
            sys.setprofile(previous)
        self.assertEqual(calls, [])
        self.assertEqual([node.tag for node in root[0]], ["strong", "code", "del", "br"])
