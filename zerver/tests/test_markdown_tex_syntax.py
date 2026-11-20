import copy
import re
import sys
import unicodedata
from importlib import import_module
from types import SimpleNamespace
from typing import cast
from unittest import SkipTest, TestCase
from unittest.mock import patch
from xml.etree.ElementTree import Element, SubElement

from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor
from typing_extensions import override

from zerver.lib import markdown
from zerver.lib.markdown import inline_stash, native_inline, tex_syntax
from zerver.lib.markdown.inline_stash import InlineProcessor
from zerver.tests import test_markdown_native_inline


class MarkdownTexSyntaxTest(TestCase):
    @override
    def setUp(self) -> None:
        if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
            raise SkipTest("Native TeX syntax requires the CPython 3.12 reference")
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "find_markdown_tex"):
            raise SkipTest("Rust extension does not support TeX syntax")

    def pattern(self) -> re.Pattern[str]:
        md = markdown.ZulipMarkdown(linkifiers=[], linkifiers_key=0, email_gateway=False)
        pattern: object = md.inlinePatterns["tex"].compiled_re
        if isinstance(pattern, tex_syntax.TexPattern):
            return pattern.fallback
        assert isinstance(pattern, re.Pattern)
        return pattern

    def matched(self, pattern: re.Pattern[str] | tex_syntax.TexPattern, data: str) -> re.Match[str]:
        result = pattern.match(data)
        assert result is not None
        return result

    def test_legacy_match_captures_and_offsets(self) -> None:
        reference = self.pattern()
        native = tex_syntax.TexPattern(reference)
        for data in ["$$x$$", "📝 $$α_1$$ end\n", r"$$x\$$", "plain", "\ud800$$x$$"]:
            with (
                self.subTest(data=data),
                patch.object(tex_syntax, "_rust_tex_syntax", self.extension),
                patch.object(
                    self.extension, "find_markdown_tex", wraps=self.extension.find_markdown_tex
                ) as find,
            ):
                expected = reference.match(data)
                actual = native.match(data)
                find.assert_called_once()
                if expected is None:
                    self.assertIsNone(actual)
                    continue
                assert actual is not None
                self.assertEqual(actual.groups(), expected.groups())
                groups: list[int | str] = [0, 1, 2, 3, 4, "body"]
                for group in groups:
                    self.assertEqual(actual.group(group), expected.group(group))
                self.assertEqual((actual.start(), actual.end()), expected.span())
                invalid_groups: list[int | str] = [-1, 5, "missing"]
                for group in invalid_groups:
                    with self.assertRaisesRegex(IndexError, "no such group"):
                        actual.group(group)

    def test_disabled_and_older_wheels_keep_the_reference(self) -> None:
        reference = self.pattern()
        with patch.object(tex_syntax, "_rust_tex_syntax", None):
            self.assertIs(tex_syntax.tex_pattern(reference), reference)
        with patch.object(tex_syntax, "_rust_tex_syntax", SimpleNamespace()):
            wrapped = tex_syntax.tex_pattern(reference)
            self.assertEqual(
                self.matched(wrapped, "$$x$$").groups(),
                self.matched(reference, "$$x$$").groups(),
            )

    def test_changed_pattern_flags_or_runtime_delegate_without_calling_rust(self) -> None:
        reference = self.pattern()
        with (
            patch.object(tex_syntax, "_rust_tex_syntax", self.extension),
            patch.object(self.extension, "find_markdown_tex") as find,
        ):
            for fallback, content in [
                (re.compile("changed"), "changed"),
                (re.compile(reference.pattern, re.ASCII), "$$x$$"),
            ]:
                wrapped = tex_syntax.TexPattern(fallback)
                self.assertEqual(
                    self.matched(wrapped, content).groups(),
                    self.matched(fallback, content).groups(),
                )
            for runtime in [
                SimpleNamespace(implementation=SimpleNamespace(name="pypy")),
                SimpleNamespace(
                    implementation=SimpleNamespace(name="cpython"), version_info=(3, 14)
                ),
            ]:
                with patch.object(tex_syntax, "sys", runtime):
                    self.assertEqual(
                        self.matched(tex_syntax.TexPattern(reference), "$$x$$").groups(),
                        self.matched(reference, "$$x$$").groups(),
                    )
            find.assert_not_called()

    def test_changed_unicode_version_delegates(self) -> None:
        reference = self.pattern()
        with (
            patch.object(tex_syntax, "_rust_tex_syntax", self.extension),
            patch.object(unicodedata, "unidata_version", "changed"),
        ):
            self.assertEqual(
                self.matched(tex_syntax.TexPattern(reference), "$$x$$").groups(),
                self.matched(reference, "$$x$$").groups(),
            )

    def test_full_rendering_preserves_calls_html_and_metadata(self) -> None:
        samples = [
            r"**formula** $$\frac{1}{2}$$ &amp; example.com $$invalid$$",
            r"`$$atomic$$` [$$label$$](https://example.com) $$<script>$$",
            "$$x$$ :smile: <time:2024-W01>\n\n$$y$$",
            "a$$x$$ $$y$$سلام $$_x$$ $$$z$$ $$a\nb$$",
        ]
        for content in samples:
            results = []
            for backend in [None, self.extension]:
                with (
                    self.subTest(content=content, native=backend is not None),
                    patch.object(tex_syntax, "_rust_tex_syntax", backend),
                    patch.object(inline_stash, "_rust_native_inline", None),
                    patch.object(
                        markdown,
                        "render_tex",
                        side_effect=lambda body, is_inline: (
                            None if body == "invalid" else "<span>fixed &amp; math</span>"
                        ),
                    ) as render,
                    patch.object(
                        self.extension, "find_markdown_tex", wraps=self.extension.find_markdown_tex
                    ) as find,
                ):
                    result = markdown.do_convert(content, no_previews=True)
                    results.append((result, render.call_args_list))
                    self.assertNotIn("<script>", result.rendered_content)
                    self.assertEqual(find.call_count > 0, backend is not None)
            self.assertEqual(results[0], results[1])

    def test_native_planner_fallback_does_not_render_or_mutate_state(self) -> None:
        native = import_module("_zulip_backend")
        helper = test_markdown_native_inline.MarkdownNativeInlineTest()
        for content, accepted in [("**before** &amp; example.com", True), ("&amp; $$x$$", False)]:
            with (
                self.subTest(content=content),
                patch.object(tex_syntax, "_rust_tex_syntax", self.extension),
            ):
                md = helper.markdown()
                processor = InlineProcessor(md)
                md.treeprocessors.register(processor, "inline", 25)
                root = Element("div")
                SubElement(root, "p").text = content
                processor.stashed_nodes = {}
                processor.parent_map = {}
                processor.ancestors = ["sentinel"]
                before = helper.snapshot(processor, root)
                metadata = copy.deepcopy(md.zulip_rendering_result)
                with patch.object(
                    markdown, "render_tex", return_value="<span>math</span>"
                ) as render:
                    self.assertEqual(
                        native_inline.try_run(
                            processor,
                            root,
                            ["outer"],
                            native.plan_markdown_native_inline,
                            native.plan_markdown_native_inline_media,
                            native.plan_markdown_native_inline_html,
                            native.plan_markdown_native_inline_autolinks,
                        ),
                        accepted,
                    )
                    render.assert_not_called()
                    if not accepted:
                        self.assertEqual(helper.snapshot(processor, root), before)
                        self.assertEqual(md.zulip_rendering_result, metadata)
                        PythonInlineProcessor.run(processor, root, ["outer"])
                        render.assert_called_once_with("x", is_inline=True)
                        self.assertEqual(md.htmlStash.rawHtmlBlocks, ["<span>math</span>", "&amp;"])

    def test_tex_wrapper_on_another_handler_delegates(self) -> None:
        helper = test_markdown_native_inline.MarkdownNativeInlineTest()
        md = helper.markdown()
        pattern = md.inlinePatterns["backtick"]
        pattern.compiled_re = cast(re.Pattern[str], tex_syntax.TexPattern(pattern.compiled_re))
        root = Element("div")
        SubElement(root, "p").text = "plain"
        native = self.extension
        processor = InlineProcessor(md)
        with patch.object(markdown, "render_tex") as render:
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
            render.assert_not_called()
