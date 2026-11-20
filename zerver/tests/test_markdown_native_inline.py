import copy
import random
import re
import sys
from collections.abc import Callable
from types import FrameType, FunctionType
from typing import cast
from unittest import TestCase, mock
from urllib import parse as urlparse
from xml.etree.ElementTree import Element, SubElement

from markdown import inlinepatterns, util
from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor
from markdown.util import AtomicString

from zerver.lib.markdown import (
    EMOJI_REGEX,
    DbData,
    MessageRenderingResult,
    ZulipMarkdown,
    do_convert,
    inline_stash,
    native_channels,
    native_inline,
    native_media,
    native_mentions,
)
from zerver.lib.markdown.inline_stash import InlineProcessor
from zerver.lib.mention import (
    ChannelInfo,
    ChannelTopicInfo,
    FullNameInfo,
    MentionBackend,
    MentionData,
)
from zerver.lib.thumbnail import AttachmentData, MarkdownImageMetadata
from zerver.models import Message, NamedUserGroup, Realm
from zerver.models.realm_emoji import EmojiInfo
from zerver.models.streams import StreamTopicsPolicyEnum


class MarkdownNativeInlineTest(TestCase):
    def markdown(self) -> ZulipMarkdown:
        md = ZulipMarkdown(linkifiers=[], linkifiers_key=0, email_gateway=False)
        md.zulip_db_data = None
        md.zulip_realm = None
        md.zulip_rendering_result = MessageRenderingResult(
            "sentinel",
            False,
            False,
            {901},
            {902},
            {"word"},
            {"url"},
            {903},
            ["previous", "previous"],
            {"spinner"},
            True,
            True,
        )
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
                list(node.attrib.items()),
                [index(child) for child in node],
            )
            for node in nodes
        ]
        return (
            tree,
            stash,
            parents,
            processor.ancestors,
            processor.md.htmlStash.html_counter,
            processor.md.htmlStash.rawHtmlBlocks[:],
        )

    def compare(
        self,
        build: Callable[[], Element],
        *,
        accepted: bool | None = True,
        configure: Callable[[ZulipMarkdown], None] | None = None,
    ) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        reference_md = self.markdown()
        if configure is not None:
            configure(reference_md)
        expected = PythonInlineProcessor(reference_md)
        root = build()
        expected.md.treeprocessors.register(expected, "inline", 25)
        expected.run(root, ["outer"])
        reference = self.snapshot(expected, root)
        native_md = self.markdown()
        if configure is not None:
            configure(native_md)
        processor = InlineProcessor(native_md)
        processor.md.treeprocessors.register(processor, "inline", 25)
        root = build()
        before = [(node, node.text, node.tail, list(node)) for node in root.iter()]
        metadata = copy.deepcopy(native_md.zulip_rendering_result)
        result = native_inline.try_run(
            processor,
            root,
            ["outer"],
            native.plan_markdown_native_inline,
            native.plan_markdown_native_inline_media,
            getattr(native, "plan_markdown_native_inline_html", None),
        )
        if accepted is not None:
            self.assertEqual(result, accepted)
        if not result:
            self.assertEqual(native_md.zulip_rendering_result, metadata)
            self.assertEqual(
                before, [(node, node.text, node.tail, list(node)) for node in root.iter()]
            )
            PythonInlineProcessor.run(processor, root, ["outer"])
        self.assertEqual(reference, self.snapshot(processor, root))
        self.assertEqual(reference_md.zulip_rendering_result, native_md.zulip_rendering_result)

    def test_native_entities_and_complete_html_stash(self) -> None:
        samples = [
            "&amp; &lt; &gt; &quot; &apos; &copy; &unknown; &123;",
            "&#0; &#999999999999999999999; &#xD800; &#x10ffff; &#x110000;",
            "&; &#; &#x; &#Xff; &é; &#۱۲; &#xＦＦ; &a_b; &amp &amp\n;",
            "&&amp; &bad&amp; &#xg&amp; فارسی &amp;;",
            "**&amp;** `&copy;` ~~&#x1F600;~~ [a &copy;](https://example.com/?a&b)",
            "<time:1900-02-29> &lt;time:bad&gt; &amp;",
            "&amp; \x02klzzwxh:0000\x03 &#1;",
        ]
        for value in samples:
            with self.subTest(value=value):

                def configure(md: ZulipMarkdown) -> None:
                    md.htmlStash.store("previous")
                    md.htmlStash.store("previous")
                    md.htmlStash.html_counter = 99

                def build(value: str = value) -> Element:
                    root = Element("div")
                    paragraph = SubElement(root, "p")
                    paragraph.text = value
                    shared = SubElement(paragraph, "span")
                    shared.text = AtomicString(value)
                    shared.tail = value
                    SubElement(root, "section").append(shared)
                    return root

                self.compare(build, configure=configure)

    def test_native_entities_commit_mentions_media_and_html_together(self) -> None:
        self.require_native_media()

        def configure(md: ZulipMarkdown) -> None:
            self.media_data(md)
            md.htmlStash.store("previous")

        def build() -> Element:
            root = Element("div")
            SubElement(
                root, "p"
            ).text = "@**alice** @*devs* ![picture](/user_uploads/1/picture) &copy;"
            SubElement(root, "p").text = "&#123; <time:1900-02-29>"
            return root

        self.compare(build, configure=configure)

    def test_native_entities_late_failure_preserves_html_stash(self) -> None:
        def configure(md: ZulipMarkdown) -> None:
            self.media_data(md)
            md.htmlStash.store("previous")

        def build() -> Element:
            root = Element("div")
            SubElement(root, "p").text = "@**alice** ![picture](/user_uploads/1/picture) &copy;"
            SubElement(root, "p").text = "$$x$$"
            return root

        self.compare(build, configure=configure, accepted=False)

    def test_native_entities_budget_and_legacy_extension(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(root, "p").text = "&amp;"
        self.assertFalse(
            native_inline.try_run(
                processor,
                root,
                None,
                native.plan_markdown_native_inline,
                native.plan_markdown_native_inline_media,
            )
        )
        self.assertEqual(md.htmlStash.rawHtmlBlocks, [])
        self.assertEqual(root[0].text, "&amp;")

        def build() -> Element:
            root = Element("div")
            SubElement(root, "p").text = "&amp; " * 2000
            return root

        self.compare(build, accepted=False)

    def test_native_entities_guard_helpers_and_stash(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(root, "p").text = "**before** &amp;"
        for target, name, replacement in [
            (md.inlinePatterns["entity"], "unescape", mock.Mock()),
            (md.inlinePatterns["entity"], "backslash_unescape", mock.Mock()),
            (md.htmlStash, "store", mock.Mock()),
            (md.htmlStash, "get_placeholder", mock.Mock()),
            (md.htmlStash, "html_counter", -1),
            (md.htmlStash, "html_counter", True),
            (md.htmlStash, "html_counter", 1_000_001),
            (md.htmlStash, "rawHtmlBlocks", ()),
            (util, "HTML_PLACEHOLDER", "changed"),
            (util, "INLINE_PLACEHOLDER_RE", re.compile("&amp;")),
            (md.treeprocessors["unescape"], "RE", re.compile("&amp;")),
        ]:
            with (
                self.subTest(name=name, replacement=replacement),
                mock.patch.object(target, name, replacement),
            ):
                self.assertFalse(
                    native_inline.try_run(
                        processor,
                        root,
                        None,
                        native.plan_markdown_native_inline,
                        native.plan_markdown_native_inline_media,
                        native.plan_markdown_native_inline_html,
                    )
                )
                if isinstance(replacement, mock.Mock):
                    replacement.assert_not_called()
        self.assertEqual(root[0].text, "**before** &amp;")
        self.assertEqual(md.htmlStash.rawHtmlBlocks, [])

    def test_native_entities_do_not_call_python_handlers(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        blocks = md.htmlStash.rawHtmlBlocks
        root = Element("div")
        SubElement(root, "p").text = "&amp; **&copy;** &#xFF;"
        forbidden = {
            inlinepatterns.HtmlInlineProcessor.handleMatch.__code__,
            *(callback.__code__ for callback in native_inline._ENTITY_CALLBACKS),
        }
        calls = []

        def profile(frame: FrameType, event: str, arg: object) -> None:
            if event == "call" and frame.f_code in forbidden:
                calls.append(frame.f_code.co_name)

        previous = sys.getprofile()
        try:
            sys.setprofile(profile)
            self.assertTrue(
                native_inline.try_run(
                    processor,
                    root,
                    None,
                    native.plan_markdown_native_inline,
                    native.plan_markdown_native_inline_media,
                    native.plan_markdown_native_inline_html,
                )
            )
        finally:
            sys.setprofile(previous)
        self.assertEqual(calls, [])
        self.assertIs(md.htmlStash.rawHtmlBlocks, blocks)
        self.assertEqual(blocks, ["&amp;", "&copy;", "&#xFF;"])
        self.assertEqual(md.htmlStash.html_counter, 3)

    def test_native_timestamps_and_complete_state(self) -> None:
        samples = [
            "0001-01-01",
            "9999-12-31",
            "2000-02-29",
            "1900-02-29",
            "0000-01-01",
            "2024-00-01",
            "2024-13-01",
            "2024-04-31",
            "2024-01-00",
            "2024-03-01T00:00:00+03:30",
            "2023-12-31T23:59:59-00:00:01",
            "2024-01-01T12.5",
            "2024-01-01T12:34,5",
            "2024-01-01T12:34:56.123456789Z",
            "2024-01-01💡12:34:56",
            "2024-01-01\n12:34:56",
            "2024-01-01012:34:56",
            "2024-01-01T12+00:60",
            "2024-01-01T12+00:00:99",
            "2024-01-01T12+00:00:00.5",
            "2024-01-01T00+00:00:01.5",
            "2024-01-01T24",
            "2024-01-01T12:60",
            "2024-01-01T12:34:60",
            "2024-01-01T12+24",
            "2024-01-01T12-23:60",
            "0001-01-01T00+00:00:01",
            "9999-12-31T23:59:59-00:00:01",
        ]
        for value in samples:
            with self.subTest(value=value):

                def build(value: str = value) -> Element:
                    root = Element("div")
                    paragraph = SubElement(root, "p")
                    paragraph.text = f"**before** <time:{value}> <time:{value}> **after**"
                    shared = SubElement(paragraph, "span", {"title": "original"})
                    shared.text = AtomicString(f"<time:{value}>")
                    shared.tail = f"<time:{value}>"
                    SubElement(root, "section").append(shared)
                    return root

                self.compare(build, accepted=None)

    def test_native_timestamp_delegation_is_atomic(self) -> None:
        for value in [
            "",
            "not a date <&>",
            "20240101T123",
            "2024-W01-1T12:3456",
            "2024-01-01T12:3456",
            "2024-01-01T12:",
            "2024-01-01T12Zx",
            "۲۰۲۴-01-01",
            "2024-01-01T12.1a",
            "2024-01-01T12+01:02:03.",
        ]:
            with self.subTest(value=value):

                def build(value: str = value) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = f"**earlier** <time:2024-01-01T12Z> <time:{value}>"
                    return root

                self.compare(build, accepted=False)

    def test_native_timestamp_work_budget_preserves_state(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        processor = InlineProcessor(md)
        root = Element("div")
        SubElement(root, "p").text = "<time:2024-01-01> " * 2000
        before = root[0].text
        rendering = copy.deepcopy(md.zulip_rendering_result)
        self.assertFalse(
            native_inline.try_run(
                processor,
                root,
                None,
                native.plan_markdown_native_inline,
            )
        )
        self.assertEqual(root[0].text, before)
        self.assertEqual(list(root[0]), [])
        self.assertEqual(md.zulip_rendering_result, rendering)
        self.assertFalse(hasattr(processor, "stashed_nodes"))

    def test_native_compact_timestamps(self) -> None:
        for value in [
            "20240101",
            "20240101T123456Z",
            "20240101T1234.5+0330",
            "2024-01-01T123456+000001.5",
            "20240101💡12:34:56-000099",
            "202401010123456+000000.5",
            "20230229",
            "20240101T240000",
            "20240101T12-2360",
            "00010101T00+000001",
            "99991231T235959-000001",
        ]:
            with self.subTest(value=value):

                def build(value: str = value) -> Element:
                    root = Element("div")
                    SubElement(
                        root, "p"
                    ).text = f"**before** <time:{value}> <time:{value}> **after**"
                    return root

                self.compare(build)

    def test_native_timestamp_commits_media_and_mentions_together(self) -> None:
        self.require_native_media()
        for value, accepted in [
            ("2024-01-01T12Z", True),
            ("2020-W53-7T12+03:30", True),
            ("2024-W01-1T12:3456", False),
        ]:
            with self.subTest(value=value):

                def build(value: str = value) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = "@**alice** ![picture](/user_uploads/1/picture)"
                    SubElement(root, "p").text = f"<time:{value}>"
                    return root

                self.compare(build, configure=self.media_data, accepted=accepted)

    def test_native_week_timestamps(self) -> None:
        for value in [
            "0001-W01-1",
            "9999-W52-5",
            "2020-W53-7",
            "2015-W01",
            "2019-W01-1",
            "2024W01",
            "2024W011",
            "2020W537T235959-000001",
            "2024-W01-1123",
            "2024W01123",
            "2024W0112345",
            "2024W0112345.5",
            "2024W0112345+01",
            "2024-W01-1💡00+000001.5",
            "2024-W01\n12Z",
            "0000-W01-1",
            "2024-W00-1",
            "2024-W54-1",
            "2021-W53-1",
            "2020-W53-0",
            "2020-W53-8",
            "9999-W52-6",
            "0001-W01-1T00+000001",
            "9999-W52-5T235959-000001",
            "2024W011234",
            "2024W01123456",
        ]:
            with self.subTest(value=value):

                def build(value: str = value) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = f"**before** <time:{value}> **after**"
                    return root

                self.compare(build)

    def test_native_week_timestamp_separator_delegation(self) -> None:
        for value in [
            "2024-W01-",
            "2024-W01-112",
            "2024-W01-112:34",
            "2024W01-1",
            "2024-W011",
            "2024-W01T",
            "2024-W01-💡12",
            "2024W",
        ]:
            with self.subTest(value=value):

                def build(value: str = value) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = f"<time:2020-W53-7T12Z> <time:{value}>"
                    return root

                self.compare(build, accepted=False)

    def test_native_timestamp_helper_and_pattern_guards(self) -> None:
        from zerver.lib import markdown

        native = inline_stash._rust_native_inline
        if native is None or sys.version_info[:2] != (3, 12):
            self.skipTest("Native timestamps require the CPython 3.12 reference contract")
        md = self.markdown()
        processor = InlineProcessor(md)
        root = Element("div")
        SubElement(root, "p").text = "**before** <time:2024-01-01T12Z>"
        for target, name, value in [
            (markdown, "datetime", object()),
            (markdown, "timezone", object()),
            (markdown, "Element", object()),
            (util, "AtomicString", object()),
            (md.inlinePatterns["timestamp"], "compiled_re", re.compile("custom")),
            (
                md.inlinePatterns["timestamp"],
                "compiled_re",
                re.compile(r"^(.*?)<time:(?P<time>[^>]*?)>(.*)$"),
            ),
        ]:
            with self.subTest(name=name), mock.patch.object(target, name, value):
                self.assertFalse(
                    native_inline.try_run(
                        processor,
                        root,
                        None,
                        native.plan_markdown_native_inline,
                    )
                )
                self.assertEqual(root[0].text, "**before** <time:2024-01-01T12Z>")

    def test_native_timestamp_registry_order_and_exclusions(self) -> None:
        for reverse in (False, True):

            def configure(md: ZulipMarkdown, reverse: bool = reverse) -> None:
                md.inlinePatterns.register(
                    md.inlinePatterns["timestamp"], "timestamp", 200 if reverse else 75
                )
                md.inlinePatterns["timestamp"].__dict__["ANCESTOR_EXCLUDES"] = ("CODE",)

            def build() -> Element:
                root = Element("div")
                SubElement(root, "p").text = "`<time:2024-01-01T12Z>` <time:2024-01-01T12Z>"
                SubElement(root, "code").text = "<time:2024-01-01T12Z>"
                SubElement(root, "p").text = "<time:unclosed"
                return root

            self.compare(build, configure=configure)

    def test_native_timestamp_does_not_call_python_parser(self) -> None:
        from zerver.lib.markdown import Timestamp

        native = inline_stash._rust_native_inline
        if native is None or sys.version_info[:2] != (3, 12):
            self.skipTest("Native timestamps require the CPython 3.12 reference contract")
        md = self.markdown()
        processor = InlineProcessor(md)
        root = Element("div")
        SubElement(root, "p").text = "<time:2024-03-01T00+03:30> <time:1900-02-29>"
        SubElement(root, "p").text = "<time:2020-W53-7T00+03:30>"
        calls = []

        def profile(frame: FrameType, event: str, arg: object) -> None:
            if event == "call" and frame.f_code is Timestamp.handleMatch.__code__:
                calls.append("handler")
            elif event == "c_call" and getattr(arg, "__name__", None) in (
                "fromisoformat",
                "astimezone",
                "isoformat",
            ):
                calls.append("datetime parser")

        previous = sys.getprofile()
        try:
            sys.setprofile(profile)
            self.assertTrue(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
        finally:
            sys.setprofile(previous)
        self.assertEqual(calls, [])
        self.assertEqual(root[0][0].attrib, {"datetime": "2024-02-29T20:30:00Z"})
        self.assertIsInstance(root[0][0].text, AtomicString)
        self.assertEqual(root[0][0].tail, " &lt;time:1900-02-29&gt;")
        self.assertEqual(root[1][0].attrib, {"datetime": "2021-01-02T20:30:00Z"})

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

    def test_named_emoji_catalog_and_inline_state(self) -> None:
        from zerver.lib.markdown import name_to_codepoint

        samples = [
            "سلام :smile: **:thumbs_up:** `:smile:` :zulip:",
            ":smile::smile: :unknown: :+1: :-1:",
            ":unknown_***bold***: :smile:\n:zulip:",
            ":::smile::: :SMILE: : :smile :smile::missing:",
            ":a-b+c_123: :a.b: :a/b: :a\tb:",
        ]
        samples.extend(f":{name}:" for name in name_to_codepoint)
        for value in samples:
            with self.subTest(value=value):

                def build(value: str = value) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = value
                    return root

                self.compare(
                    build,
                    accepted=all(
                        match.group().isascii() for match in re.finditer(EMOJI_REGEX, value)
                    ),
                )

    def test_named_emoji_helpers_and_catalogs_delegate(self) -> None:
        from zerver.lib import markdown

        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        root = Element("div")
        SubElement(root, "p").text = "**earlier** :smile:"
        processor = InlineProcessor(self.markdown())
        for name in ("make_emoji", "make_realm_emoji"):
            with mock.patch.object(markdown, name) as callback:
                self.assertFalse(
                    native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
                )
                callback.assert_not_called()
        for value in [42, "\ud800", AtomicString("1f604")]:
            with (
                self.subTest(value=value),
                mock.patch.dict(markdown.name_to_codepoint, smile=value),
            ):
                self.assertFalse(
                    native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
                )
        with mock.patch.object(processor.md, "zulip_db_data", object()):
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
        self.assertEqual(root[0].text, "**earlier** :smile:")
        self.assertFalse(hasattr(processor, "stashed_nodes"))

    def test_named_emoji_ascii_boundaries(self) -> None:
        def configure(md: ZulipMarkdown) -> None:
            registry = cast(native_inline.RegistryInternals, md.inlinePatterns)
            for name in list(registry._data):
                if name not in {
                    "backtick",
                    "strong_em",
                    "strong",
                    "emphasis",
                    "del",
                    "not_strong",
                    "nl",
                    "emoji",
                }:
                    md.inlinePatterns.deregister(name)

        for codepoint in range(128):
            separator = chr(codepoint)
            value = f":unknown{separator}name: :smile:{separator}:zulip:"
            with self.subTest(codepoint=codepoint):

                def build(value: str = value) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = value
                    return root

                self.compare(build, configure=configure)
                if separator in ".[":
                    self.compare(
                        build, accepted=separator == "[" and sys.version_info[:2] == (3, 12)
                    )

    def emoji_data(self, names: list[str]) -> DbData:
        return DbData(
            mention_data=mock.Mock(),
            realm_url="https://example.com",
            realm_alert_words_automaton=None,
            active_realm_emoji={
                name: EmojiInfo(
                    id=str(index),
                    name=name,
                    source_url=f'/user_uploads/{name}/<&>".png',
                    deactivated=False,
                    author_id=None,
                    still_url=None,
                )
                for index, name in enumerate(names)
            },
            sent_by_bot=False,
            stream_names={},
            topic_info={},
            translate_emoticons=False,
            user_upload_previews=AttachmentData(audio_path_ids=set(), image_metadata={}),
        )

    def channel_data(self) -> DbData:
        data = self.emoji_data([])
        backend = MentionBackend(1)
        backend.stream_cache = {
            "dev tools": ChannelInfo(42, 142, True, StreamTopicsPolicyEnum.inherit.value),
            "empty": ChannelInfo(17, 117, True, StreamTopicsPolicyEnum.empty_topic_only.value),
        }
        mentions = MentionData.__new__(MentionData)
        mentions.mention_backend = backend
        mentions.full_name_info = {}
        mentions.user_id_info = {}
        mentions.user_group_name_info = {}
        mentions.user_group_members = {}
        data.mention_data = mentions
        data.stream_names = {"dev tools": 42, "empty": 17, "dev tools>t@1": 99, "empty>no": 123}
        data.topic_info = {
            ChannelTopicInfo("dev tools", "t"): 123,
            ChannelTopicInfo("empty", ""): 77,
        }
        return data

    def test_native_channel_links_and_complete_state(self) -> None:
        def configure(md: ZulipMarkdown) -> None:
            md.zulip_db_data = self.channel_data()
            md.zulip_realm = Realm(topics_policy=StreamTopicsPolicyEnum.allow_empty_topic.value)

        samples = [
            "#**dev tools** #**hidden**",
            "#**dev tools>t@0001**",
            "#**empty>** #**empty>@۱٢3**",
            "#**dev tools>t** #**dev tools>unknown** #**empty>no**",
            "[#**dev tools**] <#**dev tools**",
            "`#**dev tools>t**` #**dev tools>t@1**",
            "**#**empty>****\n#**dev tools>@۰۰1**",
            "#**bad #**dev tools>t**",
        ]
        pieces = [
            "#**dev tools**",
            "#**dev tools>t**",
            "#**dev tools>t@1**",
            "#**empty>**",
            "#**empty>no**",
            "#**hidden**",
            "#**empty>@۱٢3**",
            "#**",
            "**",
            "`",
            "\n",
            " ",
            "نام",
            "[",
            "]",
            ">",
            "@",
            "1",
        ]
        required = len(samples)
        rng = random.Random(962832)
        samples.extend("".join(rng.choices(pieces, k=rng.randrange(25))) for _ in range(400))
        for number, text in enumerate(samples):
            with self.subTest(text=text):

                def build(text: str = text) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = text
                    return root

                self.compare(
                    build, configure=configure, accepted=True if number < required else None
                )
                self.compare(build, accepted=True if number < required else None)

    def test_channel_registry_priority_and_inherited_topic_policy(self) -> None:
        for reverse in [False, True]:
            for realm_policy in [
                StreamTopicsPolicyEnum.allow_empty_topic.value,
                StreamTopicsPolicyEnum.empty_topic_only.value,
            ]:

                def configure(
                    md: ZulipMarkdown, realm_policy: int = realm_policy, reverse: bool = reverse
                ) -> None:
                    md.zulip_db_data = self.channel_data()
                    md.zulip_realm = Realm(topics_policy=realm_policy)
                    if reverse:
                        md.inlinePatterns.register(md.inlinePatterns["stream"], "stream", 89)

                def build() -> Element:
                    root = Element("div")
                    SubElement(
                        root, "p"
                    ).text = "#**dev tools>t@1** #**dev tools>t** #**dev tools>**"
                    return root

                with self.subTest(reverse=reverse, realm_policy=realm_policy):
                    self.compare(build, configure=configure)

    def test_channel_snapshot_does_not_fetch_deferred_realm_policy(self) -> None:
        md = self.markdown()
        md.zulip_db_data = self.channel_data()
        md.zulip_realm = Realm()
        del md.zulip_realm.topics_policy
        with mock.patch.object(Realm, "refresh_from_db") as fetch:
            self.assertIsNone(native_channels.snapshot(md))
        fetch.assert_not_called()
        md.zulip_realm = None
        self.assertIsNone(native_channels.snapshot(md))
        md.zulip_db_data.mention_data.mention_backend.stream_cache.clear()
        snapshot = native_channels.snapshot(md)
        assert snapshot is not None
        self.assertEqual(snapshot[0]["dev tools"], (42, False))
        self.assertEqual(snapshot[1]["empty", ""], 77)
        self.assertIn("۱", snapshot[3])

    def test_channel_helpers_and_data_delegate_without_mutation(self) -> None:
        from zerver.lib import markdown, url_encoding

        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        md.zulip_db_data = self.channel_data()
        md.zulip_realm = Realm(topics_policy=StreamTopicsPolicyEnum.allow_empty_topic.value)
        processor = InlineProcessor(md)
        root = Element("div")
        SubElement(root, "p").text = "**earlier** #**dev tools>t**"
        processor.stashed_nodes = {"existing": AtomicString("sentinel")}
        processor.parent_map = {root[0]: root}
        processor.ancestors = ["sentinel"]
        before = self.snapshot(processor, root)

        def delegates() -> None:
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
            self.assertEqual(self.snapshot(processor, root), before)

        for owner, name in [
            (markdown.StreamTopicMessageProcessor, "find_stream_id"),
            (markdown.StreamTopicMessageProcessor, "is_empty_topic_only_channel"),
            (markdown.StreamTopicPattern, "get_with_operand"),
            (MentionData, "get_channel_info"),
            (markdown, "encode_channel"),
            (markdown, "encode_hash_component"),
            (url_encoding, "encode_hash_component"),
            (markdown.channel_links, "channel_link"),
            (urlparse, "quote"),
            (urlparse, "quote_from_bytes"),
        ]:
            with self.subTest(name=name), mock.patch.object(owner, name) as callback:
                delegates()
                callback.assert_not_called()
        with mock.patch.dict(url_encoding.hash_replacements, {".": ".BAD"}):
            delegates()
        with mock.patch.object(markdown, "TOPIC_LINK_SYNTAX_FOR_DISPLAY", "custom"):
            delegates()
        with mock.patch.object(Message, "EMPTY_TOPIC_FALLBACK_NAME", "\ud800"):
            delegates()
        with mock.patch.dict(md.zulip_db_data.stream_names, {"bad": 2**63}):
            delegates()
        with mock.patch.dict(
            md.zulip_db_data.topic_info, {ChannelTopicInfo("dev tools", "t"): 2**63}
        ):
            delegates()
        with mock.patch.object(md.zulip_db_data.mention_data.mention_backend, "stream_cache", []):
            delegates()
        with mock.patch.dict(
            md.zulip_db_data.mention_data.mention_backend.stream_cache, {"dev tools": object()}
        ):
            delegates()

    def test_channel_snapshot_rejects_nonstandard_and_excessive_data(self) -> None:
        md = self.markdown()
        data = self.channel_data()
        md.zulip_db_data = data
        md.zulip_realm = Realm(topics_policy=StreamTopicsPolicyEnum.allow_empty_topic.value)
        for owner, name, value in [
            (md, "zulip_db_data", object()),
            (data, "stream_names", []),
            (data, "topic_info", []),
            (data, "mention_data", object()),
            (data.mention_data, "mention_backend", object()),
            (data, "stream_names", dict.fromkeys(map(str, range(100_001)), 1)),
            (data, "stream_names", {"x" * 1_000_001: 1}),
            (data, "topic_info", {ChannelTopicInfo("dev tools", "x" * 1_000_001): 1}),
            (Message, "EMPTY_TOPIC_FALLBACK_NAME", "x" * 1_000_001),
        ]:
            with self.subTest(name=name), mock.patch.object(owner, name, value):
                self.assertIsNone(native_channels.snapshot(md))
        for name, channel_id in [("bad", True), ("\ud800", 1), (AtomicString("subclass"), 1)]:
            with self.subTest(name=name), mock.patch.dict(data.stream_names, {name: channel_id}):
                self.assertIsNone(native_channels.snapshot(md))
        for key, operand in [
            (object(), 1),
            (ChannelTopicInfo("x", "\ud800"), 1),
            (ChannelTopicInfo("x", "t"), True),
        ]:
            with self.subTest(key=key), mock.patch.dict(data.topic_info, {key: operand}):
                self.assertIsNone(native_channels.snapshot(md))
        info = data.mention_data.mention_backend.stream_cache["dev tools"]
        with mock.patch.object(info, "topics_policy", "custom"):
            self.assertIsNone(native_channels.snapshot(md))
        with mock.patch.object(Realm, "topics_policy", new_callable=mock.PropertyMock) as policy:
            self.assertIsNone(native_channels.snapshot(md))
            policy.assert_not_called()
        with mock.patch.dict(data.topic_info, {ChannelTopicInfo("dev tools", "t"): None}):
            snapshot = native_channels.snapshot(md)
            assert snapshot is not None
            self.assertNotIn(("dev tools", "t"), snapshot[1])

    def test_channel_pattern_identity_and_work_limit_delegate(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        processor = InlineProcessor(md)
        for name in ["stream", "topic", "stream_topic_message"]:
            pattern = md.inlinePatterns[name]
            root = Element("div")
            SubElement(root, "p").text = "**earlier** #**dev tools**"
            with mock.patch.object(pattern, "compiled_re", re.compile("custom")):
                self.assertFalse(
                    native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
                )
            self.assertEqual(root[0].text, "**earlier** #**dev tools**")
        root = Element("div")
        SubElement(root, "p").text = "**earlier** " + "#**unknown>t** " * 10_000
        text = root[0].text
        self.assertFalse(
            native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
        )
        self.assertEqual(root[0].text, text)
        self.assertEqual(list(root[0]), [])

    def test_channel_plan_does_not_call_python_handlers_or_lookups(self) -> None:
        from zerver.lib.markdown import _native_channel_callbacks

        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        md.zulip_db_data = self.channel_data()
        md.zulip_realm = Realm(topics_policy=StreamTopicsPolicyEnum.allow_empty_topic.value)
        processor = InlineProcessor(md)
        root = Element("div")
        SubElement(root, "p").text = "#**dev tools>t** #**empty>@۱٢01**"
        forbidden = {callback.__code__ for callback in _native_channel_callbacks}
        calls: list[str] = []

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
        self.assertEqual([node.get("class") for node in root[0]], ["stream-topic", "message-link"])

    def test_realm_emoji_overrides_and_attributes(self) -> None:
        def configure(md: ZulipMarkdown) -> None:
            md.zulip_db_data = self.emoji_data(["smile", "zulip", "custom_name"])

        rng = random.Random(87631)
        samples = ["**:smile:** :zulip: :custom_name: :unknown:"]
        pieces = [":smile:", ":zulip:", ":custom_name:", ":unknown:", "*", "`", "\n", "فارسی", " "]
        samples.extend("".join(rng.choices(pieces, k=12)) for _ in range(200))
        for value in samples:
            with self.subTest(value=value):

                def build(value: str = value) -> Element:
                    root = Element("div")
                    paragraph = SubElement(root, "p", {"title": "unchanged"})
                    paragraph.text = value
                    SubElement(paragraph, "span").tail = ":smile:"
                    return root

                self.compare(build, configure=configure)

    def test_live_realm_catalog_and_invalid_source_delegate(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        md.zulip_db_data = self.emoji_data(["smile"])
        processor = InlineProcessor(md)
        for source in ["/custom/first.png", "/custom/second.png", "\ud800"]:
            md.zulip_db_data.active_realm_emoji["smile"]["source_url"] = source
            root = Element("div")
            SubElement(root, "p").text = ":smile:"
            accepted = native_inline.try_run(
                processor, root, None, native.plan_markdown_native_inline
            )
            self.assertEqual(accepted, source != "\ud800")
            if accepted:
                self.assertEqual(root[0][0].get("src"), source)
            else:
                self.assertEqual(root[0].text, ":smile:")
                self.assertEqual(list(root[0]), [])
        md.zulip_db_data.active_realm_emoji.clear()
        root = Element("div")
        SubElement(root, "p").text = ":smile:"
        self.assertTrue(
            native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
        )
        self.assertEqual(root[0][0].tag, "span")

    def test_emoticons_enabled_disabled_and_boundaries(self) -> None:
        from zerver.lib.emoji import EMOTICON_CONVERSIONS

        def configure(md: ZulipMarkdown) -> None:
            md.zulip_db_data = self.emoji_data(["slight_smile", "smile"])
            md.zulip_db_data.translate_emoticons = enabled
            # Other handlers have separate contracts for punctuation and links.
            for name in ["autolink", "link", "image", "audio", "emoji"]:
                md.inlinePatterns.deregister(name, strict=False)

        for enabled in [False, True]:
            for emoticon in EMOTICON_CONVERSIONS:
                for boundary in [*(chr(i) for i in range(128)), "فارسی", "\u00a0", "\u2028"]:
                    for value in [f"{boundary}{emoticon}", f"{emoticon}{boundary}"]:
                        with self.subTest(enabled=enabled, value=value):

                            def build(value: str = value) -> Element:
                                root = Element("div")
                                SubElement(root, "p").text = value
                                return root

                            self.compare(build, configure=configure)

    def test_emoticons_inline_state_and_live_setting(self) -> None:
        def configure(md: ZulipMarkdown) -> None:
            md.zulip_db_data = self.emoji_data(["slight_smile"])
            md.zulip_db_data.translate_emoticons = enabled

        samples = [
            ":) (: :( <3 :| :/ ;) :D :o :O :p :P",
            "**:)** `:)` ~~:D~~\n:) :smile:",
            "سلام :) و (: پایان",
            ":) :):) (:) :)! :)\r :)\u00a0",
        ]
        rng = random.Random(962819)
        pieces = [":)", "(:", ":D", ":(", "<3", ":/", "*", "`", " ", "\n", "فارسی"]
        samples.extend("".join(rng.choices(pieces, k=12)) for _ in range(100))
        for enabled in [False, True]:
            for value in samples:
                with self.subTest(enabled=enabled, value=value):

                    def build(value: str = value) -> Element:
                        root = Element("div")
                        paragraph = SubElement(root, "p")
                        paragraph.text = value
                        SubElement(paragraph, "span").tail = " :)"
                        return root

                    self.compare(
                        build, accepted=True if value in samples[:4] else None, configure=configure
                    )

    def test_emoticon_custom_configuration_delegates_before_mutation(self) -> None:
        from zerver.lib import emoji, markdown

        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        md.zulip_db_data = self.emoji_data([])
        md.zulip_db_data.translate_emoticons = True
        processor = InlineProcessor(md)
        root = Element("div")
        SubElement(root, "p").text = "**earlier** :)"
        with mock.patch.object(markdown, "translate_emoticons") as callback:
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
            callback.assert_not_called()
        for replacement in [42, "\ud800", "\\1", AtomicString(":smile:")]:
            with (
                self.subTest(replacement=replacement),
                mock.patch.dict(emoji.EMOTICON_CONVERSIONS, {":)": replacement}),
            ):
                self.assertFalse(
                    native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
                )
        with mock.patch.dict(emoji.EMOTICON_CONVERSIONS, {"": ":smile:"}):
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
        self.assertEqual(root[0].text, "**earlier** :)")
        self.assertEqual(list(root[0]), [])
        self.assertFalse(hasattr(processor, "stashed_nodes"))

    def test_live_emoticon_setting(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        data = self.emoji_data([])
        processor = InlineProcessor(md)
        for enabled in [True, False, True, None]:
            md.zulip_db_data = None if enabled is None else data
            data.translate_emoticons = bool(enabled)
            root = Element("div")
            SubElement(root, "p").text = ":)"
            self.assertTrue(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
            if enabled:
                self.assertEqual(root[0][0].text, ":slight_smile:")
                self.assertEqual(root[0][0].get("class"), "emoji emoji-1f642")
            else:
                self.assertEqual(list(root[0]), [])
                self.assertEqual(root[0].text, ":)")

    def test_live_emoticon_catalog_and_unknown_target(self) -> None:
        from zerver.lib import emoji

        def configure(md: ZulipMarkdown) -> None:
            md.zulip_db_data = self.emoji_data([])
            md.zulip_db_data.translate_emoticons = True

        def build() -> Element:
            root = Element("div")
            SubElement(root, "p").text = ":) :D"
            return root

        for replacement in [":slight_smile:", ":smile:"]:
            with mock.patch.dict(emoji.EMOTICON_CONVERSIONS, {":)": replacement}):
                self.compare(build, configure=configure)
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        configure(md)
        root = build()
        with mock.patch.dict(emoji.EMOTICON_CONVERSIONS, {":)": ":unknown_target:"}):
            self.assertFalse(
                native_inline.try_run(
                    InlineProcessor(md), root, None, native.plan_markdown_native_inline
                )
            )
        self.assertEqual(root[0].text, ":) :D")
        self.assertEqual(list(root[0]), [])

    def test_unicode_emoji_catalog_and_state(self) -> None:
        from zerver.lib.emoji import codepoint_to_name
        from zerver.lib.emoji_utils import hex_codepoint_to_emoji

        if inline_stash._rust_native_inline is None:
            self.skipTest("Native inline engine is disabled")
        samples = [hex_codepoint_to_emoji(codepoint) for codepoint in codepoint_to_name]
        samples += [
            "سلام 😀 **👍🏽** `😀` :smile:",
            "↔ ↔️ ❤ ❤️ 1 1\u20e3 1️⃣ #️⃣ *️⃣",
            "😀\u200d😀 😀\u200d🦄 😀\u200d 😀",
            "🇦 🇦🇧 🇺🇸🇦 🇺🇸🇦🇧 🇺🇸\u200d👩",
            "👩\u200d💻 👩🏽\u200d💻 🏴\U000e0067\U000e0062\U000e007f",
            "😀\ufe0e 😀\ufe0f 😀\ufe0f\ufe0f 🏽",
        ]
        for value in samples:
            with self.subTest(value=value):

                def build(value: str = value) -> Element:
                    root = Element("div")
                    paragraph = SubElement(root, "p")
                    paragraph.text = value
                    SubElement(paragraph, "span").tail = " 😀"
                    return root

                self.compare(build)

    def test_unicode_emoji_helpers_patterns_and_version_delegate(self) -> None:
        from zerver.lib import markdown

        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        processor = InlineProcessor(self.markdown())
        root = Element("div")
        SubElement(root, "p").text = "**earlier** 😀"
        for name in ["emoji_to_hex_codepoint", "unqualify_emoji"]:
            with mock.patch.object(markdown, name) as callback:
                self.assertFalse(
                    native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
                )
                callback.assert_not_called()
        import regex

        with mock.patch.object(markdown, "TEXT_PRESENTATION_RE", regex.compile("custom")):
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
        for version in ["different", "\ud800", 42]:
            with self.subTest(version=version), mock.patch.object(regex, "__version__", version):
                self.assertFalse(
                    native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
                )
        for invalid_name in [42, "\ud800", AtomicString("grinning")]:
            with (
                self.subTest(name=invalid_name),
                mock.patch.dict(markdown.codepoint_to_name, {"1f600": invalid_name}),
            ):
                self.assertFalse(
                    native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
                )
        self.assertEqual(root[0].text, "**earlier** 😀")
        self.assertEqual(list(root[0]), [])
        self.assertFalse(hasattr(processor, "stashed_nodes"))

    def test_live_unicode_emoji_catalog(self) -> None:
        from zerver.lib import markdown

        def build() -> Element:
            root = Element("div")
            SubElement(root, "p").text = "😀 😀\u200d😀"
            return root

        for names in [
            {"1f600": "grinning"},
            {"1f600": "changed_name", "1f600-200d-1f600": "custom_sequence"},
            {},
        ]:
            with (
                self.subTest(names=names),
                mock.patch.dict(markdown.codepoint_to_name, names, clear=True),
            ):
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
            "**earlier** [link](https://[::1])",
            "**earlier** [link](https://例.example)",
            '**earlier** [link](url"(tail',
            "**earlier** :نام:",
            "**earlier** $$x$$",
        ]:
            with self.subTest(text=text):

                def build(text: str = text) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = text
                    return root

                self.compare(build, accepted=False)

    def test_native_explicit_links_and_reply_state(self) -> None:
        for content in [
            "**before** [**atomic**](https://example.com/سلام 'title') **after**",
            "[x](javascript:alert(1)) [yes](example.com)",
            "[x `code`](https://example.com/`path` 'title')",
            "[ ](#fragment '') [](/path) [x](file:///etc/passwd)",
            "[[nested]](https://example.com/(a(b)c)) [unclosed](x",
            "[Alice | hello](#narrow/dm/1/near/123 'zulip-message-reply')",
            "[Alice &#183; hello](#narrow/channel/1/near/12 'zulip-message-reply')",
            "[ | hello](#narrow/dm/1/near/123 'zulip-message-reply')",
        ]:
            with self.subTest(content=content):

                def build(content: str = content) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = content
                    return root

                self.compare(build, accepted=sys.version_info[:2] == (3, 12))

    def test_native_links_live_settings_and_local_rewriting(self) -> None:
        for files in [False, True]:
            for allowed in [("http", "file"), ("custom",)]:
                with (
                    self.subTest(files=files, allowed=allowed),
                    mock.patch("zerver.lib.markdown.allowed_schemes", allowed),
                    mock.patch("zerver.lib.markdown.settings.ENABLE_FILE_LINKS", files),
                ):

                    def build() -> Element:
                        root = Element("div")
                        SubElement(root, "p").text = (
                            "[x](file:///a) [x](custom:path) [x](example.com) "
                            "[ ](http://chat.example/#fragment) "
                            "[x](http://chat.example/user_uploads/a)"
                        )
                        return root

                    def configure(md: ZulipMarkdown) -> None:
                        md.zulip_db_data = self.emoji_data([])
                        md.zulip_db_data.realm_url = "http://chat.example"

                    self.compare(
                        build,
                        accepted=allowed != ("custom",) and sys.version_info[:2] == (3, 12),
                        configure=configure,
                    )

    def test_native_link_helpers_delegate_without_execution(self) -> None:
        from zerver.lib import markdown

        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(root, "p").text = "**before** [x](example.com)"
        link = processor.inlinePatterns["link"]
        callback = mock.Mock(side_effect=AssertionError("custom link helper ran"))
        for target, names in [
            (
                markdown,
                ["sanitize_url", "rewrite_local_links_to_relative", "urlsplit", "urlunsplit"],
            ),
            (link, ["getText", "getLink", "unescape", "zulip_specific_link_changes"]),
            (inlinepatterns, ["dequote"]),
        ]:
            for name in names:
                with self.subTest(name=name), mock.patch.object(target, name, callback):
                    self.assertFalse(
                        native_inline.try_run(
                            processor, root, None, native.plan_markdown_native_inline
                        )
                    )
                    callback.assert_not_called()
        self.assertEqual(root[0].text, "**before** [x](example.com)")

    def test_native_link_configuration_delegates_before_mutation(self) -> None:
        from django.conf import settings

        from zerver.lib import markdown

        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        link = md.inlinePatterns["link"]
        md.inlinePatterns = util.Registry()
        md.inlinePatterns.register(link, "link", 1)
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(root, "p").text = "[x](example.com)"
        invalid_data = self.emoji_data([])
        invalid_data.realm_url = "\ud800"
        for target, name, value in [
            (link, "RE_LINK", re.compile("custom")),
            (link, "RE_TITLE_CLEAN", re.compile("custom")),
            (link, "md", object()),
            (link, "zmd", object()),
            (markdown, "allowed_schemes", None),
            (markdown, "allowed_schemes", ["http", "\ud800"]),
            (settings, "ENABLE_FILE_LINKS", 1),
            (md, "zulip_db_data", object()),
            (md, "zulip_db_data", invalid_data),
            (md, "treeprocessors", object()),
        ]:
            with self.subTest(name=name, value=value), mock.patch.object(target, name, value):
                self.assertFalse(
                    native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
                )
                self.assertEqual(root[0].text, "[x](example.com)")

    def test_native_link_custom_opener_delegates_before_mutation(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None or sys.version_info[:2] != (3, 12):
            self.skipTest("Native links require the CPython 3.12 reference contract")
        md = self.markdown()
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        link = processor.inlinePatterns["link"]
        root = Element("div")
        SubElement(root, "p").text = "[x](example.com)"
        with (
            mock.patch.object(inlinepatterns, "LINK_RE", "custom"),
            mock.patch.object(link, "compiled_re", re.compile("custom", re.DOTALL | re.UNICODE)),
        ):
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
        self.assertEqual(root[0].text, "[x](example.com)")

    def test_native_link_work_limit_preserves_existing_state(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None or sys.version_info[:2] != (3, 12):
            self.skipTest("Native links require the CPython 3.12 reference contract")
        md = self.markdown()
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(root, "p").text = "**earlier** " + "[" * 20_000
        processor.stashed_nodes = {"0000": AtomicString("existing")}
        processor.parent_map = {root[0]: root}
        processor.ancestors = ["outer"]
        before = self.snapshot(processor, root)
        self.assertFalse(
            native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
        )
        self.assertEqual(self.snapshot(processor, root), before)

    def test_native_links_do_not_call_python_helpers(self) -> None:
        from zerver.lib.markdown import _native_link_callbacks

        native = inline_stash._rust_native_inline
        if native is None or sys.version_info[:2] != (3, 12):
            self.skipTest("Native links require the CPython 3.12 reference contract")
        md = self.markdown()
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(root, "p").text = (
            "[**atomic**](https://example.com/`path` '') "
            "[Alice | hi](#narrow/dm/1/near/1 'zulip-message-reply')"
        )
        forbidden = {
            callback.__code__
            for callback in _native_link_callbacks
            if hasattr(callback, "__code__")
        }
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
        self.assertEqual([node.tag for node in root[0]], ["a", "a"])

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
            ("**bold** :smile: [link](https://[::1])", False),
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
            ("emoji", "compiled_re", re.compile("custom")),
            ("emoji", "compiled_re", re.compile(r"^(.*?)(?P<syntax>:[\w\-\+]+:)(.*)$", re.ASCII)),
            ("emoji", "zmd", object()),
            ("translate_emoticons", "compiled_re", re.compile("custom")),
            ("translate_emoticons", "zmd", object()),
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
            "سلام **:smile:** :zulip: `:smile:` :unknown: :thumbs_up:",
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
        SubElement(root, "p").text = "***bold :smile:*** `:smile:` ~~strike~~\nفارسی :zulip: :) 😀"
        md = self.markdown()
        md.zulip_db_data = self.emoji_data([])
        md.zulip_db_data.translate_emoticons = True
        processor = InlineProcessor(md)
        with mock.patch.object(
            inlinepatterns.SimpleTagPattern, "handleMatch", autospec=True
        ) as callback:
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
            callback.assert_not_called()
        from zerver.lib.markdown import _native_emoji_callbacks, _native_inline_handlers

        forbidden = {
            callback.__code__
            for callbacks in _native_inline_handlers.values()
            for callback in callbacks
        }
        forbidden.update(
            callback.__code__ for callback in inline_stash._inline_engine_callbacks.values()
        )
        forbidden.update(callback.__code__ for callback in _native_emoji_callbacks)
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
        self.assertEqual(
            [node.tag for node in root[0]], ["strong", "code", "del", "br", "img", "span", "span"]
        )

    def mention_data(self, md: ZulipMarkdown) -> None:
        mentions = MentionData.__new__(MentionData)
        alice = FullNameInfo(42, "Alice <&> فارسی", True)
        inactive = FullNameInfo(43, "Inactive", False)
        mentions.full_name_info = {"alice": alice, "inactive": inactive}
        mentions.user_id_info = {42: alice, 43: inactive}
        mentions.user_group_name_info = {
            "devs": NamedUserGroup(
                id=52, name="Devs <&>", deactivated=False, is_system_group=False
            ),
            "old": NamedUserGroup(id=53, name="Old", deactivated=True, is_system_group=False),
            "role:everyone": NamedUserGroup(
                id=54, name="role:everyone", deactivated=False, is_system_group=True
            ),
        }
        md.zulip_db_data = self.emoji_data([])
        md.zulip_db_data.mention_data = mentions

    def test_native_mentions_and_notification_union(self) -> None:
        for content in [
            "@**ALICE** @**Alice <&> فارسی|42** @**|۴۲** @**|٤٢\n**",
            "@**alice** @_**alice** @**inactive** @*devs* @_*devs* @*old*",
            "@**all** @**topic** @_**channel** @**Everyone** @**missing** @*missing*",
            "@**wrong|42** @**alice|42** @**|999999999999999999999999999** @**|00042**",
            "**@**alice**** `@**all** @*devs*` @**alice**\n@*devs*",
            "@*role:everyone* @_**topic** @**everyone**",
        ]:
            with self.subTest(content=content):

                def build(content: str = content) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = content
                    SubElement(root, "p").text = AtomicString("@**all** @*devs*")
                    return root

                self.compare(build, configure=self.mention_data)

    def test_native_mentions_preserve_no_database_context(self) -> None:
        def build() -> Element:
            root = Element("div")
            SubElement(root, "p").text = "@**all** @**topic** @**|42** @*devs*"
            return root

        self.compare(build)

    def test_native_mentions_late_failure_discards_notifications(self) -> None:
        for suffix in ["&copy; $$x$$", "$$x$$", "[x](https://[::1])"]:
            with self.subTest(suffix=suffix):

                def build(suffix: str = suffix) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = "@**alice** @**all** @*devs*"
                    SubElement(root, "p").text = suffix
                    return root

                self.compare(build, accepted=False, configure=self.mention_data)

    def test_native_mention_helpers_delegate_without_execution(self) -> None:
        from zerver.lib import markdown, mention

        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        self.mention_data(md)
        assert md.zulip_db_data is not None
        processor = InlineProcessor(md)
        root = Element("div")
        SubElement(root, "p").text = "@**alice** @*devs*"
        callback = mock.Mock(side_effect=AssertionError("custom mention helper ran"))
        for target, names in [
            (
                md.zulip_db_data.mention_data,
                ["get_user_by_name", "get_user_by_id", "get_user_group"],
            ),
            (
                mention,
                ["user_mention_matches_topic_wildcard", "user_mention_matches_stream_wildcard"],
            ),
            (markdown, ["get_user_group_mention_display_name", "build_mention_span"]),
            (sys, ["get_int_max_str_digits"]),
        ]:
            for name in names:
                with self.subTest(name=name), mock.patch.object(target, name, callback):
                    self.assertFalse(
                        native_inline.try_run(
                            processor, root, None, native.plan_markdown_native_inline
                        )
                    )
                    callback.assert_not_called()
                    self.assertEqual(root[0].text, "@**alice** @*devs*")
                    self.assertFalse(hasattr(processor, "stashed_nodes"))

    def test_native_mention_snapshot_guards(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        self.mention_data(md)
        assert md.zulip_db_data is not None
        mentions = md.zulip_db_data.mention_data
        root = Element("div")
        SubElement(root, "p").text = "@**alice** @*devs*"
        processor = InlineProcessor(md)
        deferred = NamedUserGroup(id=52, name="Devs")
        del vars(deferred)["deactivated"]
        for target, name, value in [
            (md, "zulip_db_data", object()),
            (md.zulip_db_data, "mention_data", object()),
            (md, "zulip_rendering_result", object()),
            (md.zulip_rendering_result, "mentions_user_ids", [901]),
            (md.zulip_rendering_result, "mentions_user_group_ids", {"902"}),
            (md.zulip_rendering_result, "mentions_topic_wildcard", 1),
            (mentions, "full_name_info", []),
            (mentions, "full_name_info", {"alice": FullNameInfo(42, "\ud800", True)}),
            (mentions, "user_id_info", {2**40: FullNameInfo(42, "Alice", True)}),
            (mentions, "full_name_info", {"alice": FullNameInfo(42, "Alice", cast(bool, 1))}),
            (mentions, "user_group_name_info", {"devs": object()}),
            (mentions, "user_group_name_info", {"devs": deferred}),
            (
                mentions,
                "user_group_name_info",
                {
                    "devs": NamedUserGroup(
                        id=52, name="\ud800", deactivated=False, is_system_group=False
                    )
                },
            ),
        ]:
            with self.subTest(name=name, value=value), mock.patch.object(target, name, value):
                self.assertFalse(
                    native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
                )
                self.assertEqual(root[0].text, "@**alice** @*devs*")
                self.assertFalse(hasattr(processor, "stashed_nodes"))

    def test_native_mention_conversion_limit_preserves_python_error(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        self.mention_data(md)
        root = Element("div")
        SubElement(root, "p").text = "@**alice** @**|" + "0" * 641 + "**"
        processor = InlineProcessor(md)
        before = copy.deepcopy(md.zulip_rendering_result)
        previous = sys.get_int_max_str_digits()
        try:
            sys.set_int_max_str_digits(640)
            self.assertFalse(
                native_inline.try_run(processor, root, None, native.plan_markdown_native_inline)
            )
            self.assertEqual(md.zulip_rendering_result, before)
            with self.assertRaises(ValueError):
                PythonInlineProcessor.run(processor, root)
        finally:
            sys.set_int_max_str_digits(previous)

    def test_native_mentions_commit_existing_set_objects(self) -> None:
        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        self.mention_data(md)
        root = Element("div")
        SubElement(root, "p").text = "@**alice** @*devs* @**topic**"
        rendering = md.zulip_rendering_result
        users, groups = rendering.mentions_user_ids, rendering.mentions_user_group_ids
        self.assertTrue(
            native_inline.try_run(
                InlineProcessor(md), root, None, native.plan_markdown_native_inline
            )
        )
        self.assertIs(rendering.mentions_user_ids, users)
        self.assertIs(rendering.mentions_user_group_ids, groups)
        self.assertEqual(users, {901, 42})
        self.assertEqual(groups, {902, 52})
        self.assertTrue(rendering.mentions_topic_wildcard)
        self.assertFalse(rendering.mentions_stream_wildcard)

    def test_native_mention_snapshot_resource_limits(self) -> None:
        md = self.markdown()
        self.mention_data(md)
        assert md.zulip_db_data is not None
        mentions = md.zulip_db_data.mention_data
        for target, name, value in [
            (
                mentions,
                "full_name_info",
                dict.fromkeys((str(n) for n in range(100_001)), FullNameInfo(42, "Alice", True)),
            ),
            (mentions, "full_name_info", {"alice": FullNameInfo(42, "x" * 1_000_001, True)}),
            (
                mentions,
                "user_group_name_info",
                {
                    "devs": NamedUserGroup(
                        id=52, name="role:unknown", is_system_group=True, deactivated=False
                    )
                },
            ),
        ]:
            with self.subTest(name=name), mock.patch.object(target, name, value):
                self.assertIsNone(native_mentions.snapshot(md, ["@**alice** @*devs*"]))
        self.assertIsNotNone(native_mentions.snapshot(md, ["@**alice** @*devs*"]))
        with mock.patch.object(
            mentions,
            "user_group_name_info",
            {
                "devs": NamedUserGroup(
                    id=52, name="x" * 600_000, is_system_group=False, deactivated=False
                )
            },
        ):
            self.assertIsNone(native_mentions.snapshot(md, ["@*devs*"]))
        self.assertIsNone(
            native_mentions.snapshot(md, [f"@*{number}*" for number in range(100_001)])
        )
        with mock.patch.object(
            mentions,
            "user_group_name_info",
            {
                "devs": NamedUserGroup(
                    id=52, name="x" * 100_000, is_system_group=False, deactivated=False
                )
            },
        ):
            group_aliases = [
                "devs",
                "Devs",
                "dEvs",
                "deVs",
                "devS",
                "DEvs",
                "DeVs",
                "DevS",
                "dEVs",
                "dEvS",
            ]
            self.assertIsNone(
                native_mentions.snapshot(md, [" ".join(f"@*{name}*" for name in group_aliases)])
            )
        large = FullNameInfo(42, "x" * 100_000, True)
        aliases = [
            "alice",
            "Alice",
            "aLice",
            "alIce",
            "aliCe",
            "alicE",
            "ALice",
            "AlIce",
            "AliCe",
            "AlicE",
        ]
        with (
            mock.patch.object(mentions, "full_name_info", {"alice": large}),
            mock.patch.object(mentions, "user_id_info", {42: large}),
        ):
            self.assertIsNone(
                native_mentions.snapshot(md, [" ".join(f"@**{name}**" for name in aliases)])
            )

    def test_native_system_group_mentions_use_current_language(self) -> None:
        from django.utils.translation import override

        def build() -> Element:
            root = Element("div")
            SubElement(root, "p").text = "@*role:everyone* @_*role:everyone*"
            return root

        for language in ["en", "fa", "de", "ar"]:
            with self.subTest(language=language), override(language):
                self.compare(build, configure=self.mention_data)

    def test_native_mentions_do_not_call_python_handlers_or_lookups(self) -> None:
        from zerver.lib.markdown import (
            UserGroupMentionPattern,
            UserMentionPattern,
            _native_inline_handlers,
            _native_mention_callbacks,
        )

        native = inline_stash._rust_native_inline
        if native is None:
            self.skipTest("Native inline engine is disabled")
        md = self.markdown()
        self.mention_data(md)
        root = Element("div")
        SubElement(root, "p").text = "@**alice** @*devs* @**topic**"
        forbidden = {
            _native_inline_handlers[cls][0].__code__
            for cls in (UserMentionPattern, UserGroupMentionPattern)
        }
        forbidden.update(callback.__code__ for callback in _native_mention_callbacks[:3])
        calls = []

        def profile(frame: FrameType, event: str, arg: object) -> None:
            if event == "call" and frame.f_code in forbidden:
                calls.append(frame.f_code.co_name)

        previous = sys.getprofile()
        try:
            sys.setprofile(profile)
            self.assertTrue(
                native_inline.try_run(
                    InlineProcessor(md), root, None, native.plan_markdown_native_inline
                )
            )
        finally:
            sys.setprofile(previous)
        self.assertEqual(calls, [])
        self.assertEqual(md.zulip_rendering_result.mentions_user_ids, {42, 901})
        self.assertEqual(md.zulip_rendering_result.mentions_user_group_ids, {52, 902})

    def media_data(self, md: ZulipMarkdown) -> None:
        self.mention_data(md)
        assert md.zulip_db_data is not None
        md.zulip_db_data.user_upload_previews = AttachmentData(
            audio_path_ids={"1/voice", "1/both", "1/voice?x#y"},
            image_metadata={
                path: MarkdownImageMetadata(None, False, 12, 24, "image/png")
                for path in ["1/picture", "1/both", "1/picture?x#y"]
            },
        )
        md.zulip_realm = Realm(string_id="zulip")
        md.zulip_rendering_result.has_image = False

    def require_native_media(self) -> None:
        if inline_stash._rust_native_inline is None or sys.version_info[:2] != (3, 12):
            self.skipTest("Native media requires the CPython 3.12 reference contract")

    def test_native_media_complete_state_and_metadata(self) -> None:
        self.require_native_media()
        for content in [
            "![picture](/user_uploads/1/picture) ![voice](/user_uploads/1/voice)",
            "![both](/user_uploads/1/both) ![both](/user_uploads/1/both)",
            "![unknown](/user_uploads/1/unknown) ![empty](/user_uploads/)",
            "![picture](/user_uploads/1/picture?x#y) ![voice](/user_uploads/1/voice?x#y)",
            "![**raw title**](https://other.example/a.mp3) ![picture](/user_uploads/1/picture '')",
            "![picture](/user_uploads/1/picture 'title') ![voice](/user_uploads/1/voice 'title')",
            "![bad](javascript:alert(1)) ![no](https://other.example/a.png)",
            "**before** @**alice** @*devs* ![picture](/user_uploads/1/picture)",
            "`![picture](/user_uploads/1/picture)` ![voice](/user_uploads/1/voice)",
        ]:
            with self.subTest(content=content):

                def build(content: str = content) -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = content
                    SubElement(root, "code").text = AtomicString(content)
                    shared = SubElement(root, "span")
                    shared.tail = content
                    SubElement(root, "section").append(shared)
                    return root

                self.compare(
                    build, configure=self.media_data, accepted="javascript:" not in content
                )

    def test_native_media_absent_and_empty_previews(self) -> None:
        self.require_native_media()
        for context in [False, True]:

            def configure(md: ZulipMarkdown, context: bool = context) -> None:
                if context:
                    md.zulip_db_data = self.emoji_data([])

            def build() -> Element:
                root = Element("div")
                SubElement(
                    root, "p"
                ).text = "![voice](/user_uploads/1/a.mp3) ![picture](/user_uploads/1/picture)"
                return root

            self.compare(build, configure=configure)

    def test_native_media_runtime_mime_and_camo(self) -> None:
        import mimetypes

        from django.test import override_settings

        self.require_native_media()
        mime_db = cast(mimetypes.MimeTypes, vars(mimetypes)["_db"])
        for camo in ["", "https://proxy.example/"]:
            with (
                override_settings(CAMO_URI=camo, CAMO_KEY="فارسی" * 40),
                mock.patch.dict(mime_db.types_map[1], {".custom": "audio/mpeg"}),
            ):

                def build() -> Element:
                    root = Element("div")
                    SubElement(root, "p").text = (
                        "![a](https://other.example/a.custom) "
                        "![b](https://other.example/a.mp3.gz) "
                        "![c](/static/sound.mp3) "
                        "![d](https://zulip.zulip.testserver/sound.mp3)"
                    )
                    return root

                self.compare(build, configure=self.media_data)

    def test_native_media_late_fallback_is_atomic(self) -> None:
        self.require_native_media()
        native = inline_stash._rust_native_inline
        assert native is not None
        md = self.markdown()
        self.media_data(md)
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(
            root, "p"
        ).text = "@**alice** ![picture](/user_uploads/1/picture) ![bad](http://[bad]/a)"
        processor.stashed_nodes = {"0000": AtomicString("existing")}
        processor.parent_map = {root[0]: root}
        processor.ancestors = ["before"]
        before = self.snapshot(processor, root)
        metadata = copy.deepcopy(md.zulip_rendering_result)
        registry = cast(native_inline.RegistryInternals, md.inlinePatterns)
        priorities = registry._priority[:]
        self.assertFalse(
            native_inline.try_run(
                processor,
                root,
                None,
                native.plan_markdown_native_inline,
                native.plan_markdown_native_inline_media,
                getattr(native, "plan_markdown_native_inline_html", None),
            )
        )
        self.assertEqual(self.snapshot(processor, root), before)
        self.assertEqual(md.zulip_rendering_result, metadata)
        self.assertEqual(registry._priority, priorities)
        self.assertFalse(registry._is_sorted)

    def test_native_media_preserves_attachment_list_identity(self) -> None:
        self.require_native_media()
        native = inline_stash._rust_native_inline
        assert native is not None
        md = self.markdown()
        self.media_data(md)
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(
            root, "p"
        ).text = "![picture](/user_uploads/1/picture) ![unknown](/user_uploads/1/unknown)"
        paths = md.zulip_rendering_result.potential_attachment_path_ids
        self.assertTrue(
            native_inline.try_run(
                processor,
                root,
                None,
                native.plan_markdown_native_inline,
                native.plan_markdown_native_inline_media,
                getattr(native, "plan_markdown_native_inline_html", None),
            )
        )
        self.assertIs(md.zulip_rendering_result.potential_attachment_path_ids, paths)
        self.assertEqual(
            paths, ["previous", "previous", "1/picture", "1/unknown", "1/picture", "1/unknown"]
        )
        self.assertTrue(md.zulip_rendering_result.has_image)

    def test_native_media_helper_and_pattern_guards(self) -> None:
        from zerver.lib import camo, markdown

        self.require_native_media()
        native = inline_stash._rust_native_inline
        assert native is not None
        md = self.markdown()
        self.media_data(md)
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(root, "p").text = "![picture](/user_uploads/1/picture)"
        callback = mock.Mock(side_effect=AssertionError("custom media helper called"))
        for owner, name in [
            (markdown, "maybe_add_attachment_path_id"),
            (markdown, "get_camo_url"),
            (markdown, "guess_type"),
            (markdown, "is_static_or_current_realm_url"),
            (camo, "generate_camo_url"),
            (camo, "get_camo_url"),
        ]:
            with self.subTest(name=name), mock.patch.object(owner, name, callback):
                self.assertIsNone(native_media.snapshot(md))
        for name in ["audio", "image"]:
            with (
                self.subTest(pattern=name),
                mock.patch.object(md.inlinePatterns[name], "zulip_specific_src_changes", callback),
            ):
                self.assertFalse(
                    native_inline.try_run(
                        processor,
                        root,
                        None,
                        native.plan_markdown_native_inline,
                        native.plan_markdown_native_inline_media,
                        getattr(native, "plan_markdown_native_inline_html", None),
                    )
                )
        callback.assert_not_called()
        self.assertIsNotNone(native_media.snapshot(md))
        self.assertEqual(root[0].text, "![picture](/user_uploads/1/picture)")

    def test_native_media_snapshot_guards_and_limits(self) -> None:
        from django.test import override_settings

        self.require_native_media()
        md = self.markdown()
        self.media_data(md)
        assert md.zulip_db_data is not None
        previews = md.zulip_db_data.user_upload_previews
        for name, guard_value in [
            ("STATIC_URL", None),
            ("STATIC_URL", "\ud800"),
            ("CAMO_KEY", "\ud800"),
            ("CAMO_URI", "\ud800"),
            ("LOCAL_UPLOADS_DIR", None),
        ]:
            with (
                self.subTest(name=name, value=guard_value),
                override_settings(**{name: guard_value}),
            ):
                self.assertIsNone(native_media.snapshot(md))
        with override_settings(CAMO_URI="https://proxy.example/", CAMO_KEY=None):
            self.assertIsNone(native_media.snapshot(md))
        deferred_realm = Realm()
        del vars(deferred_realm)["string_id"]
        with mock.patch.object(md, "zulip_realm", deferred_realm):
            self.assertIsNone(native_media.snapshot(md))
        with mock.patch.object(Realm, "host", property(lambda _: "changed")):
            self.assertIsNone(native_media.snapshot(md))
        for name, preview_value in [
            ("audio_path_ids", ["1/voice"]),
            ("audio_path_ids", {"\ud800"}),
            ("image_metadata", {"1/picture": object()}),
            (
                "image_metadata",
                {str(i): previews.image_metadata["1/picture"] for i in range(100_001)},
            ),
        ]:
            with self.subTest(name=name), mock.patch.object(previews, name, preview_value):
                self.assertIsNone(native_media.snapshot(md))
        metadata = previews.image_metadata["1/picture"]
        for name, metadata_value in [
            ("original_width_px", True),
            ("original_width_px", 2**2001),
            ("original_height_px", "24"),
            ("original_content_type", "\ud800"),
        ]:
            with self.subTest(name=name), mock.patch.object(metadata, name, metadata_value):
                self.assertIsNone(native_media.snapshot(md))
        with mock.patch.object(md.zulip_rendering_result, "potential_attachment_path_ids", ()):
            self.assertIsNone(native_media.snapshot(md))
        self.assertIsNotNone(native_media.snapshot(md))

    def test_native_media_does_not_call_python_handlers(self) -> None:
        from zerver.lib.markdown import _native_media_audio_callbacks, _native_media_callbacks

        self.require_native_media()
        native = inline_stash._rust_native_inline
        assert native is not None
        md = self.markdown()
        self.media_data(md)
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(
            root, "p"
        ).text = "![picture](/user_uploads/1/picture) ![a](https://other.example/a.mp3)"
        forbidden = {
            callback.__code__
            for callback in (*_native_media_callbacks, *_native_media_audio_callbacks)
            if isinstance(callback, FunctionType)
        }
        calls = []

        def profile(frame: FrameType, event: str, arg: object) -> None:
            if event == "call" and frame.f_code in forbidden:
                calls.append(frame.f_code.co_name)

        previous = sys.getprofile()
        try:
            sys.setprofile(profile)
            self.assertTrue(
                native_inline.try_run(
                    processor,
                    root,
                    None,
                    native.plan_markdown_native_inline,
                    native.plan_markdown_native_inline_media,
                    getattr(native, "plan_markdown_native_inline_html", None),
                )
            )
        finally:
            sys.setprofile(previous)
        self.assertEqual(calls, [])
        self.assertEqual([node.tag for node in root[0]], ["img", "audio"])

    def test_native_media_mime_snapshot_guards(self) -> None:
        import mimetypes

        from zerver.lib import markdown

        self.require_native_media()
        md = self.markdown()
        self.media_data(md)
        mime_db = cast(mimetypes.MimeTypes, vars(mimetypes)["_db"])
        guards: list[tuple[object, str, object]] = [
            (mimetypes, "_db", None),
            (mime_db, "guess_type", mock.Mock()),
            (mime_db, "types_map", ({},)),
            (mime_db, "suffix_map", []),
            (mime_db, "suffix_map", dict.fromkeys((str(i) for i in range(100_001)), ".mp3")),
            (mime_db, "suffix_map", {".custom": "\ud800"}),
            (markdown, "AUDIO_INLINE_MIME_TYPES", {"audio/mpeg"}),
            (markdown, "AUDIO_INLINE_MIME_TYPES", ["\ud800"]),
            (urlparse, "uses_params", ["\ud800"]),
        ]
        for owner, name, value in guards:
            with self.subTest(name=name), mock.patch.object(owner, name, value):
                self.assertIsNone(native_media.snapshot(md))
        self.assertIsNotNone(native_media.snapshot(md))

    def test_native_media_snapshot_budget_and_data_types(self) -> None:
        from django.test import override_settings

        self.require_native_media()
        md = self.markdown()
        self.media_data(md)
        assert md.zulip_db_data is not None
        with mock.patch.object(md, "zulip_db_data", object()):
            self.assertIsNone(native_media.snapshot(md))
        with mock.patch.object(md.zulip_db_data, "user_upload_previews", object()):
            self.assertIsNone(native_media.snapshot(md))
        with override_settings(REALM_HOSTS={}, EXTERNAL_HOST="x" * 600_000):
            self.assertIsNone(native_media.snapshot(md))
        large_images = {
            str(i): MarkdownImageMetadata(None, False, 2**1999, 2**1999, None) for i in range(1000)
        }
        with mock.patch.object(
            md.zulip_db_data.user_upload_previews, "image_metadata", large_images
        ):
            self.assertIsNone(native_media.snapshot(md))
        metadata = md.zulip_db_data.user_upload_previews.image_metadata["1/picture"]
        with mock.patch.object(metadata, "original_content_type", None):
            snapshot = native_media.snapshot(md)
            assert snapshot is not None and snapshot[2] is not None
            self.assertEqual(snapshot[2]["1/picture"], ("12", "24", None))

    def test_native_media_legacy_planner_and_changed_regex_delegate(self) -> None:
        from django.test import override_settings

        from zerver.lib import markdown

        self.require_native_media()
        native = inline_stash._rust_native_inline
        assert native is not None
        md = self.markdown()
        self.media_data(md)
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(root, "p").text = "![picture](/user_uploads/1/picture)"
        self.assertFalse(
            native_inline.try_run(
                processor,
                root,
                None,
                native.plan_markdown_native_inline,
            )
        )
        with override_settings(LOCAL_UPLOADS_DIR=None):
            self.assertFalse(
                native_inline.try_run(
                    processor,
                    root,
                    None,
                    native.plan_markdown_native_inline,
                    native.plan_markdown_native_inline_media,
                    getattr(native, "plan_markdown_native_inline_html", None),
                )
            )
        pattern = md.inlinePatterns["image"]
        changed = re.compile(r"custom", re.DOTALL)
        with (
            mock.patch.object(pattern, "compiled_re", changed),
            mock.patch.object(inlinepatterns, "IMAGE_LINK_RE", "custom"),
        ):
            self.assertFalse(
                native_inline.try_run(
                    processor,
                    root,
                    None,
                    native.plan_markdown_native_inline,
                    native.plan_markdown_native_inline_media,
                    getattr(native, "plan_markdown_native_inline_html", None),
                )
            )
        self.assertEqual(markdown._native_media_pattern, r"\!\[")
        self.assertEqual(root[0].text, "![picture](/user_uploads/1/picture)")

    def test_native_media_dimensions_and_missing_content_type(self) -> None:
        self.require_native_media()
        for width, height, content_type in [(0, -2, None), (2**1999, 2**1998, "")]:

            def configure(
                md: ZulipMarkdown,
                width: int = width,
                height: int = height,
                content_type: str | None = content_type,
            ) -> None:
                self.media_data(md)
                assert md.zulip_db_data is not None
                md.zulip_db_data.user_upload_previews.image_metadata["1/picture"] = (
                    MarkdownImageMetadata(None, False, width, height, content_type)
                )

            def build() -> Element:
                root = Element("div")
                SubElement(root, "p").text = "![picture](/user_uploads/1/picture)"
                return root

            self.compare(build, configure=configure)

    def test_native_media_cloud_public_root_and_camo(self) -> None:
        from django.test import override_settings

        from zerver.lib import upload
        from zerver.lib.upload.s3 import S3UploadBackend

        self.require_native_media()
        backend = object.__new__(S3UploadBackend)
        backend.public_upload_url_base = "https://cdn.example/public/"
        for root in ["https://cdn.example/public/", "https://cdn.example:443/public/"]:
            backend.public_upload_url_base = root
            for reverse in [False, True]:
                with (
                    override_settings(LOCAL_UPLOADS_DIR=None, CAMO_URI="https://proxy.example/"),
                    mock.patch.object(upload, "_upload_backend", backend),
                ):

                    def configure(md: ZulipMarkdown, reverse: bool = reverse) -> None:
                        self.media_data(md)
                        if reverse:
                            md.inlinePatterns.register(md.inlinePatterns["audio"], "audio", 56)

                    def build(root: str = root) -> Element:
                        tree = Element("div")
                        SubElement(tree, "p").text = (
                            f"![public]({root}audio.mp3) "
                            "![other](https://cdn.example/public-evil/audio.mp3) "
                            "![picture](/user_uploads/1/picture) "
                            "![both](/user_uploads/1/both) @**alice**"
                        )
                        return tree

                    self.compare(build, configure=configure)

    def test_native_media_cloud_snapshot_guards(self) -> None:
        from django.test import override_settings

        from zerver.lib import subdomains, upload
        from zerver.lib.upload.s3 import S3UploadBackend

        self.require_native_media()
        md = self.markdown()
        self.media_data(md)
        backend = object.__new__(S3UploadBackend)
        backend.public_upload_url_base = "https://cdn.example/public/"
        callback = mock.Mock(side_effect=AssertionError("custom upload callback ran"))
        with (
            override_settings(LOCAL_UPLOADS_DIR=None),
            mock.patch.object(upload, "_upload_backend", backend),
        ):
            for owner, name in [
                (upload, "get_upload_backend"),
                (upload, "get_public_upload_root_url"),
                (subdomains, "get_public_upload_root_url"),
                (S3UploadBackend, "get_public_upload_root_url"),
                (backend, "get_public_upload_root_url"),
            ]:
                with self.subTest(name=name), mock.patch.object(owner, name, callback):
                    self.assertIsNone(native_media.snapshot(md))
            with mock.patch.object(
                S3UploadBackend,
                "public_upload_url_base",
                property(lambda _: "changed"),
                create=True,
            ):
                self.assertIsNone(native_media.snapshot(md))
            for bad_backend in [None, object()]:
                with mock.patch.object(upload, "_upload_backend", bad_backend):
                    self.assertIsNone(native_media.snapshot(md))
            for bad_root in [
                None,
                12,
                "",
                "https://cdn.example/public",
                "\ud800/",
                "x" * 1_000_001 + "/",
            ]:
                with (
                    self.subTest(root_type=type(bad_root)),
                    mock.patch.object(backend, "public_upload_url_base", bad_root),
                ):
                    self.assertIsNone(native_media.snapshot(md))
            self.assertIsNotNone(native_media.snapshot(md))
        callback.assert_not_called()

    def test_native_media_cloud_snapshot_does_not_initialize_backend(self) -> None:
        from django.test import override_settings

        from zerver.lib import upload
        from zerver.lib.upload.s3 import S3UploadBackend

        self.require_native_media()
        native = inline_stash._rust_native_inline
        assert native is not None
        md = self.markdown()
        self.media_data(md)
        backend = object.__new__(S3UploadBackend)
        backend.public_upload_url_base = "https://cdn.example/public/"
        processor = InlineProcessor(md)
        md.treeprocessors.register(processor, "inline", 25)
        root = Element("div")
        SubElement(root, "p").text = "![public](https://cdn.example/public/audio.mp3)"
        paths = md.zulip_rendering_result.potential_attachment_path_ids[:]
        forbidden = {callback.__code__ for callback in native_media._UPLOAD_CALLBACKS}
        calls = []

        def profile(frame: FrameType, event: str, arg: object) -> None:
            if event == "call" and frame.f_code in forbidden:
                calls.append(frame.f_code.co_name)

        with (
            override_settings(LOCAL_UPLOADS_DIR=None),
            mock.patch.object(upload, "_upload_backend", None),
            mock.patch.object(
                S3UploadBackend,
                "__init__",
                side_effect=AssertionError("backend initialization attempted"),
            ) as initialize,
        ):
            self.assertIsNone(native_media.snapshot(md))
            with mock.patch.object(upload, "_upload_backend", backend):
                previous = sys.getprofile()
                try:
                    sys.setprofile(profile)
                    self.assertTrue(
                        native_inline.try_run(
                            processor,
                            root,
                            None,
                            native.plan_markdown_native_inline,
                            native.plan_markdown_native_inline_media,
                            getattr(native, "plan_markdown_native_inline_html", None),
                        )
                    )
                finally:
                    sys.setprofile(previous)
            self.assertEqual(calls, [])
            initialize.assert_not_called()
        self.assertEqual(root[0][0].get("src"), "https://cdn.example/public/audio.mp3")
        self.assertEqual(md.zulip_rendering_result.potential_attachment_path_ids, paths)
