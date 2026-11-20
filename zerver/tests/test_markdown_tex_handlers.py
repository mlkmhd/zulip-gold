import copy
import re
from contextlib import ExitStack
from importlib import import_module
from types import SimpleNamespace
from unittest import SkipTest, TestCase
from unittest.mock import call, patch
from xml.etree.ElementTree import Element, SubElement, tostring

from django.utils.html import escape
from markdown.util import AtomicString, HtmlStash
from typing_extensions import override

from zerver.lib import markdown
from zerver.lib.markdown import fenced_code, inline_stash, native_inline
from zerver.lib.markdown.fenced_code import FencedBlockPreprocessor
from zerver.lib.markdown.inline_stash import InlineProcessor
from zerver.tests import test_markdown_native_inline


class MarkdownTexHandlersTest(TestCase):
    @override
    def setUp(self) -> None:
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "render_markdown_inline_tex"):
            raise SkipTest("Rust extension does not support TeX handlers")

    def test_inline_success_preserves_html_stash_and_empty_output(self) -> None:
        for rendered in ["", "<span>α &amp; β</span>", "\ud800"]:
            with self.subTest(rendered=rendered):
                helper = test_markdown_native_inline.MarkdownNativeInlineTest()
                md = helper.markdown()
                md.htmlStash.store("previous")
                blocks = md.htmlStash.rawHtmlBlocks
                before = copy.deepcopy(md.zulip_rendering_result)
                matched = re.match(r"(?P<body>.*)", "x", re.DOTALL)
                assert matched is not None
                with (
                    patch.object(markdown, "_rust_tex_handlers", self.extension),
                    patch.object(markdown, "render_tex", return_value=rendered) as render,
                ):
                    actual = md.inlinePatterns["tex"].handleMatch(matched)
                render.assert_called_once_with("x", is_inline=True)
                self.assertEqual(actual, "\x02wzxhzdk:1\x03")
                self.assertIs(md.htmlStash.rawHtmlBlocks, blocks)
                self.assertEqual(blocks, ["previous", rendered])
                self.assertEqual(md.htmlStash.html_counter, 2)
                self.assertEqual(md.zulip_rendering_result, before)

    def test_inline_failure_is_atomic_and_escaped_at_serialization(self) -> None:
        for body in ["<script>&\"'", "**bold** :smile: @**all**", "\ud800", ""]:
            with self.subTest(body=body):
                md = test_markdown_native_inline.MarkdownNativeInlineTest().markdown()
                md.htmlStash.store("previous")
                matched = re.match(r"(?P<body>.*)", body, re.DOTALL)
                assert matched is not None
                with (
                    patch.object(markdown, "_rust_tex_handlers", self.extension),
                    patch.object(markdown, "render_tex", return_value=None) as render,
                ):
                    actual = md.inlinePatterns["tex"].handleMatch(matched)
                render.assert_called_once_with(body, is_inline=True)
                assert isinstance(actual, Element)
                self.assertEqual(actual.tag, "span")
                self.assertEqual(actual.attrib, {"class": "tex-error"})
                self.assertIs(type(actual.text), AtomicString)
                self.assertEqual(actual.text, "$$" + body + "$$")
                self.assertEqual(md.htmlStash.rawHtmlBlocks, ["previous"])
                self.assertEqual(md.htmlStash.html_counter, 1)
                self.assertNotIn("<script>", tostring(actual, encoding="unicode"))

    def test_disabled_and_old_wheel_fallback_render_once(self) -> None:
        for extension in [None, SimpleNamespace()]:
            with self.subTest(extension=extension):
                md = test_markdown_native_inline.MarkdownNativeInlineTest().markdown()
                matched = re.match(r"(?P<body>.*)", "x")
                assert matched is not None
                with (
                    patch.object(markdown, "_rust_tex_handlers", extension),
                    patch.object(markdown, "render_tex", return_value="inline") as inline,
                    patch.object(fenced_code, "_rust_tex_handlers", extension),
                    patch.object(fenced_code, "render_tex", return_value="display") as display,
                ):
                    self.assertEqual(
                        md.inlinePatterns["tex"].handleMatch(matched), "\x02wzxhzdk:0\x03"
                    )
                    self.assertEqual(FencedBlockPreprocessor(md).format_tex("x"), "display")
                inline.assert_called_once_with("x", is_inline=True)
                display.assert_called_once_with("x", is_inline=False)

    def test_renderer_exception_propagates_without_retry(self) -> None:
        failure = RuntimeError("render failed")
        for native in [None, self.extension]:
            with self.subTest(native=native):
                md = test_markdown_native_inline.MarkdownNativeInlineTest().markdown()
                matched = re.match(r"(?P<body>.*)", "x")
                assert matched is not None
                with (
                    patch.object(markdown, "_rust_tex_handlers", native),
                    patch.object(markdown, "render_tex", side_effect=failure) as render,
                    self.assertRaises(RuntimeError) as raised,
                ):
                    md.inlinePatterns["tex"].handleMatch(matched)
                self.assertIs(raised.exception, failure)
                render.assert_called_once_with("x", is_inline=True)
                self.assertEqual(md.htmlStash.rawHtmlBlocks, [])
                self.assertEqual(md.htmlStash.html_counter, 0)

    def test_stash_failure_is_not_retried(self) -> None:
        md = test_markdown_native_inline.MarkdownNativeInlineTest().markdown()
        matched = re.match(r"(?P<body>.*)", "x")
        assert matched is not None
        with (
            patch.object(markdown, "_rust_tex_handlers", self.extension),
            patch.object(markdown, "render_tex", return_value="html") as render,
            patch.object(md.htmlStash, "store", side_effect=RuntimeError("stash failed")) as store,
            self.assertRaisesRegex(RuntimeError, "stash failed"),
        ):
            md.inlinePatterns["tex"].handleMatch(matched)
        render.assert_called_once_with("x", is_inline=True)
        store.assert_called_once_with("html")

    def test_display_paragraph_order_empty_values_and_escaping(self) -> None:
        text = "\n\nfirst\nline\n\n<&\"'>\n\n\n\nlast\n\n"
        outputs = ["", "<span>first</span>", None, None, "last", None]
        expected = None
        for native in [None, self.extension]:
            md = test_markdown_native_inline.MarkdownNativeInlineTest().markdown()
            with (
                patch.object(fenced_code, "_rust_tex_handlers", native),
                patch.object(fenced_code, "render_tex", side_effect=outputs) as render,
            ):
                actual = FencedBlockPreprocessor(md).format_tex(text)
            self.assertEqual(
                render.call_args_list,
                [call(paragraph, is_inline=False) for paragraph in text.split("\n\n")],
            )
            self.assertEqual(md.htmlStash.rawHtmlBlocks, [])
            self.assertEqual(md.htmlStash.html_counter, 0)
            self.assertIn("&lt;&amp;&quot;&#x27;&gt;", actual)
            if native is None:
                expected = actual
            else:
                self.assertEqual(actual, expected)

    def test_display_surrogates_and_html_are_preserved(self) -> None:
        md = test_markdown_native_inline.MarkdownNativeInlineTest().markdown()
        for rendered, expected in [
            (None, '<span class="tex-error">\ud800&lt;&amp;</span>'),
            ("\udfff <b>html</b>", "\udfff <b>html</b>"),
        ]:
            with (
                self.subTest(rendered=rendered),
                patch.object(fenced_code, "_rust_tex_handlers", self.extension),
                patch.object(fenced_code, "render_tex", return_value=rendered) as render,
            ):
                self.assertEqual(FencedBlockPreprocessor(md).format_tex("\ud800<&"), expected)
                render.assert_called_once_with("\ud800<&", is_inline=False)

    def test_display_exception_stops_before_later_paragraphs(self) -> None:
        failure = RuntimeError("second paragraph failed")
        for native in [None, self.extension]:
            with self.subTest(native=native):
                md = test_markdown_native_inline.MarkdownNativeInlineTest().markdown()
                with (
                    patch.object(fenced_code, "_rust_tex_handlers", native),
                    patch.object(
                        fenced_code, "render_tex", side_effect=["first", failure]
                    ) as render,
                    self.assertRaises(RuntimeError) as raised,
                ):
                    FencedBlockPreprocessor(md).format_tex("first\n\nsecond\n\nthird")
                self.assertIs(raised.exception, failure)
                self.assertEqual(
                    render.call_args_list,
                    [call("first", is_inline=False), call("second", is_inline=False)],
                )

    def test_display_reads_current_renderer_for_each_paragraph(self) -> None:
        md = test_markdown_native_inline.MarkdownNativeInlineTest().markdown()

        def later(body: str, *, is_inline: bool) -> str:
            return "later: " + body

        def first(body: str, *, is_inline: bool) -> str:
            callbacks.enter_context(patch.object(fenced_code, "render_tex", later))
            return "first: " + body

        with (
            patch.object(fenced_code, "_rust_tex_handlers", self.extension),
            patch.object(fenced_code, "render_tex", first),
            ExitStack() as callbacks,
        ):
            self.assertEqual(
                FencedBlockPreprocessor(md).format_tex("a\n\nb\n\nc"),
                "first: a\n\nlater: b\n\nlater: c",
            )

    def test_finding_unclosed_fences_does_not_render_tex(self) -> None:
        with (
            patch.object(fenced_code, "_rust_tex_handlers", self.extension),
            patch.object(fenced_code, "render_tex") as render,
            patch.object(
                self.extension,
                "render_markdown_display_tex",
                wraps=self.extension.render_markdown_display_tex,
            ) as handler,
        ):
            self.assertEqual(
                fenced_code.close_unclosed_fences("```math\nx\n```\n\n```quote\ntext", None),
                "```math\nx\n```\n\n```quote\ntext\n```",
            )
            render.assert_not_called()
            handler.assert_not_called()

    def test_callbacks_resolve_after_renderer_side_effects(self) -> None:
        md = test_markdown_native_inline.MarkdownNativeInlineTest().markdown()

        def render(body: str, *, is_inline: bool) -> None:
            callbacks.enter_context(
                patch.object(fenced_code, "escape", lambda value: escape(value) + " changed")
            )

        with (
            patch.object(fenced_code, "_rust_tex_handlers", self.extension),
            patch.object(fenced_code, "render_tex", side_effect=render),
            ExitStack() as callbacks,
        ):
            self.assertEqual(
                FencedBlockPreprocessor(md).format_tex("<&"),
                '<span class="tex-error">&lt;&amp; changed</span>',
            )

    def test_inline_stash_resolves_after_renderer_side_effects(self) -> None:
        md = test_markdown_native_inline.MarkdownNativeInlineTest().markdown()
        old_stash = md.htmlStash
        matched = re.match(r"(?P<body>.*)", "x")
        assert matched is not None

        def render(body: str, *, is_inline: bool) -> str:
            md.htmlStash = HtmlStash()
            md.htmlStash.store("from renderer")
            return "new html"

        with (
            patch.object(markdown, "_rust_tex_handlers", self.extension),
            patch.object(markdown, "render_tex", side_effect=render),
        ):
            self.assertEqual(md.inlinePatterns["tex"].handleMatch(matched), "\x02wzxhzdk:1\x03")
        self.assertEqual(old_stash.rawHtmlBlocks, [])
        self.assertEqual(md.htmlStash.rawHtmlBlocks, ["from renderer", "new html"])

    def test_native_planner_delegates_before_any_tex_callback(self) -> None:
        helper = test_markdown_native_inline.MarkdownNativeInlineTest()
        md = helper.markdown()
        root = Element("div")
        SubElement(root, "p").text = "@**all** &amp; $$x$$"
        SubElement(root, "p").text = "[unsupported](http://[bad]/)"
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        processor.stashed_nodes = {}
        processor.parent_map = {}
        processor.ancestors = []
        before = helper.snapshot(processor, root)
        metadata = copy.deepcopy(md.zulip_rendering_result)
        with (
            patch.object(markdown, "_rust_tex_handlers", self.extension),
            patch.object(markdown, "render_tex", return_value="html") as render,
        ):
            native = self.extension
            self.assertFalse(
                native_inline.try_run(
                    processor,
                    root,
                    None,
                    native.plan_markdown_native_inline,
                    native.plan_markdown_native_inline_media,
                    native.plan_markdown_native_inline_html,
                    native.plan_markdown_native_inline_autolinks,
                )
            )
            render.assert_not_called()
            self.assertEqual(helper.snapshot(processor, root), before)
            self.assertEqual(md.zulip_rendering_result, metadata)
            processor.run(root)
            render.assert_called_once_with("x", is_inline=True)

    def test_full_rendering_and_all_metadata_match(self) -> None:
        for content in [
            "**math** $$x < y$$ &amp; example.com",
            "$$invalid$$ :smile: @**all** <time:2024-W01>",
            "$$x$$\n\n```math\nfirst\n\n<&\n```\n\n$$last$$",
        ]:
            outputs = [None if "invalid" in content else "<i>math</i>"] * 10
            with (
                self.subTest(content=content),
                patch.object(markdown, "unsafe_timeout", lambda timeout, callback: callback()),
                patch.object(inline_stash, "_rust_native_inline", None),
                patch.object(markdown, "_rust_tex_handlers", None),
                patch.object(fenced_code, "_rust_tex_handlers", None),
                patch.object(markdown, "render_tex", side_effect=outputs) as expected_inline,
                patch.object(fenced_code, "render_tex", side_effect=outputs) as expected_display,
            ):
                expected = markdown.do_convert(content, no_previews=True)
            with (
                patch.object(markdown, "unsafe_timeout", lambda timeout, callback: callback()),
                patch.object(inline_stash, "_rust_native_inline", self.extension),
                patch.object(markdown, "_rust_tex_handlers", self.extension),
                patch.object(fenced_code, "_rust_tex_handlers", self.extension),
                patch.object(markdown, "render_tex", side_effect=outputs) as actual_inline,
                patch.object(fenced_code, "render_tex", side_effect=outputs) as actual_display,
            ):
                actual = markdown.do_convert(content, no_previews=True)
            self.assertEqual(actual, expected)
            self.assertEqual(actual_inline.call_args_list, expected_inline.call_args_list)
            self.assertEqual(actual_display.call_args_list, expected_display.call_args_list)
