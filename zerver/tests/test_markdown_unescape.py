import random
import re
import sys
from typing import cast
from unittest import TestCase, mock
from xml.etree.ElementTree import Element, SubElement

from markdown.treeprocessors import UnescapeTreeprocessor as PythonUnescapeTreeprocessor
from markdown.util import AtomicString

from zerver.lib.markdown import unescape
from zerver.lib.markdown.unescape import UnescapeTreeprocessor


class MarkdownUnescapeTest(TestCase):
    def compare(self, text: str) -> None:
        try:
            expected = PythonUnescapeTreeprocessor().unescape(text)
        except (TypeError, ValueError, OverflowError) as error:
            with self.assertRaises(type(error)) as actual:
                UnescapeTreeprocessor().unescape(text)
            self.assertEqual(str(actual.exception), str(error))
        else:
            result = UnescapeTreeprocessor().unescape(text)
            self.assertEqual(result, expected)
            self.assertIs(type(result), type(expected))

    def test_placeholders_unicode_and_errors(self) -> None:
        for value in [
            "0",
            "42",
            "00042",
            "۴۲",
            "4۲",
            "²",
            "-1",
            "",
            "1.0",
            "55295",
            "55296",
            "57343",
            "57344",
            "1114111",
            "1114112",
            "9" * 100,
            "0" * 129,
        ]:
            self.compare(f"فارسی \x02{value}\x03 after \x0238\x03")
        for text in ["", "plain فارسی", "\x02", "\x02\x0242\x03", "\x022\x0342\x03", "\ud800"]:
            self.compare(text)
        self.compare(AtomicString("text"))
        self.compare(AtomicString("\x0242\x03"))
        self.compare(cast(str, 3))

    def test_unchanged_exact_string_identity_and_atomic_type(self) -> None:
        unescaped = "fixture:متن 🙂".removeprefix("fixture:")
        atomic = AtomicString(unescaped)
        processor = UnescapeTreeprocessor()
        self.assertIs(processor.unescape(unescaped), unescaped)
        result = processor.unescape(atomic)
        self.assertEqual(result, unescaped)
        self.assertIs(type(result), str)
        self.assertIsNot(result, atomic)

    def test_generated_text(self) -> None:
        rng = random.Random(17)
        pieces = ["\x02", "\x03", "0", "42", "1114112", "فارسی", "۴۲", "-", "&", ""]
        for _ in range(2000):
            self.compare("".join(rng.choices(pieces, k=rng.randrange(1, 15))))

    def run_tree(
        self, use_python: bool, invalid: bool
    ) -> tuple[list[tuple[object, ...]], str | None]:
        root = Element("div", {"id": "\x0242\x03", "title": "\x0234\x03<&>"})
        root.text = AtomicString("before \x0238\x03")
        root.tail = "\x0260\x03"
        code = SubElement(root, "code", {"title": "\x0242\x03"})
        code.text = AtomicString("\x0242\x03")
        code.tail = "\x0238\x03"
        paragraph = SubElement(
            root,
            "p",
            {"first": "\x0242\x03", "last": "\x021114112\x03" if invalid else "\x0238\x03"},
        )
        paragraph.text = "\x0242\x03"
        paragraph.tail = "\x0238\x03"
        processor = PythonUnescapeTreeprocessor() if use_python else UnescapeTreeprocessor()
        error = None
        try:
            result = processor.run(root)
            self.assertIsNone(result)
        except ValueError as exc:
            error = str(exc)
        return [
            (node.tag, node.text, type(node.text), node.tail, list(node.items()))
            for node in root.iter()
        ], error

    def test_tree_order_code_text_and_partial_errors(self) -> None:
        for invalid in [False, True]:
            expected = self.run_tree(use_python=True, invalid=invalid)
            self.assertEqual(self.run_tree(use_python=False, invalid=invalid), expected)
        tree, error = self.run_tree(use_python=False, invalid=False)
        self.assertIsNone(error)
        self.assertEqual(tree[1][1], "\x0242\x03")
        self.assertIs(tree[1][2], AtomicString)
        self.assertEqual(tree[1][3], "&")

    def test_custom_callbacks_and_patterns(self) -> None:
        processor = UnescapeTreeprocessor()
        with mock.patch.object(processor, "_unescape", return_value="custom") as callback:
            self.assertEqual(processor.unescape("\x0242\x03"), "custom")
            callback.assert_called_once()
        with mock.patch.object(processor, "RE", re.compile(r"(42)")):
            self.assertEqual(processor.unescape("42"), "*")

        def custom(processor: PythonUnescapeTreeprocessor, match: re.Match[str]) -> str:
            return "custom"

        with mock.patch.object(PythonUnescapeTreeprocessor, "_unescape", custom):
            self.assertEqual(processor.unescape("\x0242\x03"), "custom")
        with mock.patch.object(PythonUnescapeTreeprocessor, "RE", re.compile(r"(42)")):
            self.assertEqual(processor.unescape("42"), "*")

    def test_integer_string_limit(self) -> None:
        previous_limit = sys.get_int_max_str_digits()
        try:
            sys.set_int_max_str_digits(640)
            self.compare("\x02" + "0" * 641 + "\x03")
        finally:
            sys.set_int_max_str_digits(previous_limit)

    def test_rust_unescape_is_used(self) -> None:
        if unescape._rust_unescape is None:
            self.skipTest("Rust unescape is disabled")
        with mock.patch.object(PythonUnescapeTreeprocessor, "unescape", side_effect=AssertionError):
            self.assertEqual(UnescapeTreeprocessor().unescape("فارسی \x0242\x03"), "فارسی *")
            self.run_tree(use_python=False, invalid=False)
