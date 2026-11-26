import random
import re
import sys
from typing import SupportsIndex, cast
from unittest import TestCase, mock
from xml.etree.ElementTree import Element

from markdown import Markdown
from markdown.postprocessors import AndSubstitutePostprocessor as PythonAndSubstitutePostprocessor
from markdown.postprocessors import RawHtmlPostprocessor as PythonRawHtmlPostprocessor
from markdown.util import AMP_SUBSTITUTE, AtomicString, HtmlStash
from typing_extensions import override

from zerver.lib.markdown import postprocessing
from zerver.lib.markdown.postprocessing import AndSubstitutePostprocessor, RawHtmlPostprocessor


class MarkdownPostprocessingTest(TestCase):
    def compare(self, text: str, blocks: list[str]) -> str:
        engine = Markdown()
        for html in blocks:
            engine.htmlStash.store(html)
        before = list(engine.htmlStash.rawHtmlBlocks)
        expected = PythonRawHtmlPostprocessor(engine).run(text)
        result = RawHtmlPostprocessor(engine).run(text)
        self.assertEqual(result, expected)
        self.assertEqual(engine.htmlStash.rawHtmlBlocks, before)
        self.assertEqual(engine.htmlStash.html_counter, len(before))
        return result

    def test_nested_placeholders_and_paragraph_wrappers(self) -> None:
        placeholder = HtmlStash().get_placeholder
        self.assertEqual(
            self.compare(f"<p>{placeholder(0)}</p>", [placeholder(1), "<pre>code</pre>"]),
            "<pre>code</pre>",
        )
        self.assertEqual(self.compare(f"<p>{placeholder(0)}</p>", ["&bogus;"]), "<p>&bogus;</p>")
        for html in [
            "<DIV>block</DIV>",
            "<p/>text",
            "<!--comment-->",
            "<?target?>",
            "<@custom>",
            "<%custom>",
            "<span>inline</span>",
            " <p>leading space</p>",
            "<p\n>newline</p>",
            "",
            "فارسی",
        ]:
            for text in [
                placeholder(0),
                f"<p>{placeholder(0)}</p>",
                f"<p> {placeholder(0)} </p>",
                f"<p><p>{placeholder(0)}</p></p>",
                f"{placeholder(0)}{placeholder(0)}",
            ]:
                self.compare(text, [html])

    def test_unknown_and_malformed_placeholders(self) -> None:
        for key in ["", "-1", "۰", "x", "00", "01", "1", "9" * 100, "0" * 129]:
            for prefix, suffix in [("", ""), ("<p>", "</p>"), ("<p>", " </p>")]:
                self.compare(f"{prefix}\x02wzxhzdk:{key}\x03{suffix}", ["replacement"])
        self.compare("\x02wzxhzdk:\x02wzxhzdk:0\x03", ["replacement"])
        self.compare("<p>\x02wzxhzdk:0\x03</p>", [])

    def test_generated_acyclic_stashes(self) -> None:
        rng = random.Random(13)
        placeholder = HtmlStash().get_placeholder
        pieces = ["", "فارسی", "&amp;", "<p>", "</p>", "<pre>", "<span>", "\x02", "\x03"]
        for _ in range(2000):
            blocks: list[str] = []
            for index in range(rng.randrange(1, 8)):
                choices = [*pieces, *(placeholder(key) for key in range(index + 1, 9))]
                blocks.append("".join(rng.choices(choices, k=rng.randrange(1, 8))))
            text = "".join(rng.choices([*pieces, *(placeholder(i) for i in range(9))], k=10))
            self.compare(text, blocks)

    def test_rendering_code_entities_and_raw_html(self) -> None:
        for content in [
            "```python\nprint('<&>')\n```\n\n&copy; &bogus;",
            "<div>\n\n**bold**\n\n</div>",
            "inline <span>html &amp;</span> and `code`",
            "    indented code <&>\n\nفارسی",
        ]:
            original = Markdown(extensions=["fenced_code", "codehilite"])
            engine = Markdown(extensions=["fenced_code", "codehilite"])
            engine.postprocessors.register(RawHtmlPostprocessor(engine), "raw_html", 30)
            engine.postprocessors.register(AndSubstitutePostprocessor(), "amp_substitute", 20)
            self.assertEqual(engine.convert(content), original.convert(content))

    def test_ampersands(self) -> None:
        for text in ["", "فارسی &amp;", AMP_SUBSTITUTE * 3, "\x02amp", "\x02AMP\x03", "\ud800"]:
            self.assertEqual(
                AndSubstitutePostprocessor().run(text), PythonAndSubstitutePostprocessor().run(text)
            )

    def test_nonstandard_stashes_and_unicode(self) -> None:
        placeholder = HtmlStash().get_placeholder(0)
        self.compare("\ud800" + placeholder, ["text"])
        self.compare(placeholder, ["\ud800"])
        self.compare(placeholder, [AtomicString("text")])
        engine = Markdown()
        processor = RawHtmlPostprocessor(engine)
        for value in [Element("p"), 0, None]:
            engine.htmlStash.rawHtmlBlocks = [cast(str, value)]
            engine.htmlStash.html_counter = 1
            self.assertEqual(
                processor.run(placeholder), PythonRawHtmlPostprocessor(engine).run(placeholder)
            )
        for counter in [-1, 0, 1, 2]:
            engine.htmlStash.rawHtmlBlocks = []
            engine.htmlStash.html_counter = counter
            if counter > 0:
                with self.assertRaises(IndexError):
                    processor.run(placeholder)
            else:
                self.assertEqual(processor.run(placeholder), placeholder)
        engine.htmlStash.html_counter = 0
        text = AtomicString("text")
        self.assertIs(processor.run(text), text)

    def test_custom_callbacks_and_strings(self) -> None:
        class CustomString(str):
            __slots__ = ()

            @override
            def replace(self, old: str, new: str, count: SupportsIndex = -1) -> str:
                return "custom replacement"

        self.assertEqual(
            AndSubstitutePostprocessor().run(CustomString("text")), "custom replacement"
        )
        engine = Markdown()
        text = engine.htmlStash.store("<pre>code</pre>")
        processor = RawHtmlPostprocessor(engine)
        with mock.patch.object(processor, "stash_to_string", return_value="custom") as callback:
            self.assertEqual(processor.run(text), "custom")
            callback.assert_called_once()
        with mock.patch.object(processor, "isblocklevel", return_value=False) as callback:
            self.assertEqual(processor.run(f"<p>{text}</p>"), "<p><pre>code</pre></p>")
            callback.assert_called_once()
        with mock.patch.object(engine, "is_block_level", return_value=False) as callback:
            self.assertEqual(processor.run(f"<p>{text}</p>"), "<p><pre>code</pre></p>")
            callback.assert_called_once()
        with mock.patch.object(processor, "BLOCK_LEVEL_REGEX", re.compile(r"^(never)")):
            self.assertEqual(processor.run(f"<p>{text}</p>"), "<p><pre>code</pre></p>")

    def test_cycles_and_deep_stashes(self) -> None:
        engine = Markdown()
        placeholder = engine.htmlStash.get_placeholder
        engine.htmlStash.store(placeholder(0))
        for processor in [PythonRawHtmlPostprocessor(engine), RawHtmlPostprocessor(engine)]:
            with self.assertRaises(RecursionError):
                processor.run(placeholder(0))
        self.compare(placeholder(0), [*(placeholder(i + 1) for i in range(110)), "end"])

    def test_python_integer_string_limit(self) -> None:
        engine = Markdown()
        engine.htmlStash.store("replacement")
        previous_limit = sys.get_int_max_str_digits()
        try:
            sys.set_int_max_str_digits(640)
            text = "\x02wzxhzdk:" + "0" * 641 + "\x03"
            with self.assertRaises(ValueError) as expected:
                PythonRawHtmlPostprocessor(engine).run(text)
            with self.assertRaises(ValueError) as actual:
                RawHtmlPostprocessor(engine).run(text)
            self.assertEqual(str(actual.exception), str(expected.exception))
        finally:
            sys.set_int_max_str_digits(previous_limit)

    def test_class_customization_preserves_callback_order(self) -> None:
        calls: list[str] = []
        engine = Markdown()
        placeholder = engine.htmlStash.store("<pre>code</pre>")
        engine.htmlStash.store("unused")
        processor = RawHtmlPostprocessor(engine)

        def isblocklevel(processor: PythonRawHtmlPostprocessor, html: str) -> bool:
            calls.append(html)
            return False

        def stash_to_string(processor: PythonRawHtmlPostprocessor, html: str) -> str:
            calls.append(html)
            return "custom"

        with mock.patch.object(PythonRawHtmlPostprocessor, "isblocklevel", isblocklevel):
            self.assertEqual(processor.run(placeholder), "<pre>code</pre>")
            self.assertEqual(calls, [])
            self.assertEqual(processor.run(f"<p>{placeholder}</p>"), "<p><pre>code</pre></p>")
            self.assertEqual(calls, ["<pre>code</pre>"])
        calls.clear()
        with mock.patch.object(PythonRawHtmlPostprocessor, "stash_to_string", stash_to_string):
            self.assertEqual(processor.run(placeholder), "custom")
            self.assertEqual(calls, ["<pre>code</pre>"])
        with mock.patch.object(PythonRawHtmlPostprocessor, "BLOCK_LEVEL_REGEX", re.compile(r"^()")):
            self.assertEqual(processor.run(placeholder), "<pre>code</pre>")
            with self.assertRaises(IndexError):
                processor.run(f"<p>{placeholder}</p>")

    def test_noop_identity_matches_original_processors(self) -> None:
        engine = Markdown()
        engine.htmlStash.store("<em>already selected</em>")
        raw = RawHtmlPostprocessor(engine)
        original_raw = PythonRawHtmlPostprocessor(engine)
        text = "prefix:ordinary فارسی text".removeprefix("prefix:")
        for value in (text, AtomicString(text)):
            expected = original_raw.run(value)
            actual = raw.run(value)
            self.assertEqual(actual, expected)
            self.assertIs(type(actual), type(expected))
            self.assertEqual(actual is value, expected is value)
        subst = AndSubstitutePostprocessor()
        self.assertIs(subst.run(text), text)
        token = AtomicString(text)
        self.assertIs(type(subst.run(token)), str)

    def test_runtime_ampersand_token_uses_reference_when_changed(self) -> None:
        with mock.patch("markdown.util.AMP_SUBSTITUTE", "alternate-placeholder"):
            self.assertEqual(AndSubstitutePostprocessor().run("alternate-placeholder"), "&")

    def test_rust_postprocessing_is_used(self) -> None:
        if postprocessing._rust_postprocessing is None:
            self.skipTest("Rust postprocessing is disabled")
        with (
            mock.patch.object(PythonRawHtmlPostprocessor, "run", side_effect=AssertionError),
            mock.patch.object(PythonAndSubstitutePostprocessor, "run", side_effect=AssertionError),
        ):
            engine = Markdown()
            placeholder = engine.htmlStash.store("<pre>code</pre>")
            self.assertEqual(
                RawHtmlPostprocessor(engine).run(f"<p>{placeholder}</p>"), "<pre>code</pre>"
            )
            self.assertEqual(AndSubstitutePostprocessor().run(AMP_SUBSTITUTE), "&")
