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
    EMOJI_REGEX,
    DbData,
    MessageRenderingResult,
    ZulipMarkdown,
    do_convert,
    inline_stash,
    native_inline,
)
from zerver.lib.markdown.inline_stash import InlineProcessor
from zerver.lib.thumbnail import AttachmentData
from zerver.models.realm_emoji import EmojiInfo


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
                list(node.attrib.items()),
                [index(child) for child in node],
            )
            for node in nodes
        ]
        return tree, stash, parents, processor.ancestors

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
        result = native_inline.try_run(
            processor, root, ["outer"], native.plan_markdown_native_inline
        )
        if accepted is not None:
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
                    self.compare(build, accepted=False)

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
            "**earlier** :نام:",
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
            ("**bold** :smile: 😀", False),
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
        SubElement(root, "p").text = "***bold :smile:*** `:smile:` ~~strike~~\nفارسی :zulip: :)"
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
            [node.tag for node in root[0]], ["strong", "code", "del", "br", "img", "span"]
        )
