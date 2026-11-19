import copy
import random
from typing import cast
from unittest import TestCase, mock
from xml.etree.ElementTree import Comment, Element, ProcessingInstruction, SubElement

from markdown import Markdown
from markdown.treeprocessors import PrettifyTreeprocessor as PythonPrettifyTreeprocessor
from markdown.util import AtomicString

from zerver.lib.markdown import prettify
from zerver.lib.markdown.prettify import PrettifyTreeprocessor


class MarkdownPrettifyTest(TestCase):
    def compare(self, root: Element, blocks: list[str] | None = None) -> Element:
        expected = copy.deepcopy(root)
        actual = copy.deepcopy(root)
        md = Markdown()
        if blocks is not None:
            md.block_level_elements = blocks
        error = None
        try:
            self.assertIsNone(PythonPrettifyTreeprocessor(md).run(expected))
        except (AttributeError, TypeError) as exc:
            error = exc
        processor = PrettifyTreeprocessor(md)
        if error is None:
            self.assertIsNone(processor.run(actual))
        else:
            with self.assertRaises(type(error)) as result:
                processor.run(actual)
            self.assertEqual(str(result.exception), str(error))
        self.assertEqual(
            [(n.tag, n.text, type(n.text), n.tail, type(n.tail), n.attrib) for n in actual.iter()],
            [
                (n.tag, n.text, type(n.text), n.tail, type(n.tail), n.attrib)
                for n in expected.iter()
            ],
        )
        return actual

    def test_blocks_inline_breaks_and_code(self) -> None:
        root = Element("div", {"title": " untouched "})
        root.text = AtomicString("\u001c\u0085")
        paragraph = SubElement(root, "p")
        paragraph.text = AtomicString("فارسی")
        paragraph.tail = AtomicString("\t")
        inline = SubElement(paragraph, "em")
        inline.text = "inline"
        inline.tail = AtomicString(" ")
        SubElement(inline, "br").tail = AtomicString("فارسی")
        pre = SubElement(inline, "pre")
        SubElement(pre, "code").text = "text\u001f\u00a0\n\n"
        result = self.compare(root)
        self.assertEqual(result.text, "\n")
        self.assertIs(type(result.text), str)
        self.assertIs(type(result[0].text), AtomicString)
        self.assertEqual(result[0][0].tail, " ")
        self.assertIs(type(result[0][0].tail), AtomicString)
        self.assertEqual(result[0][0][0].tail, "\nفارسی")
        self.assertEqual(result[0][0][1][0].text, "text\n")
        self.assertIs(type(result[0][0][1][0].text), AtomicString)

    def test_code_shapes_and_case_sensitive_special_tags(self) -> None:
        for tag in ["div", "span", "pre", "PRE", "pre/", "code", "br", "BR", "br/"]:
            for text in [None, "", "\n", "x \n", AtomicString("x\t")]:
                root = Element(tag)
                root.text = text
                root.tail = AtomicString("\n")
                for child_tag in ["code", "CODE", "p", "br"]:
                    child = SubElement(root, child_tag)
                    child.text = text
                    child.tail = text
                self.compare(root)
                SubElement(root[0], "span").text = "child"
                self.compare(root)

    def test_configured_block_elements(self) -> None:
        root = Element("DIV/")
        root.text = " "
        child = SubElement(root, "br")
        child.text = " "
        SubElement(child, "pre")
        for blocks in [[], ["div", "br", "pre"], ["br"], ["DIV"], ["div", "span"]]:
            self.compare(root, blocks)
        root.tag = "İ"
        self.compare(root, ["i\u0307", "br"])

    def test_nonstring_tags_shared_nodes_and_deep_trees(self) -> None:
        root = Element("div")
        root.append(Comment("comment"))
        root.append(ProcessingInstruction("target", "value"))
        root.append(Element(cast(str, None)))
        root.append(Element(cast(str, 42)))
        SubElement(root[-1], "br").tail = "after"
        self.compare(root)
        root.remove(root[-1])
        self.compare(root)
        child = SubElement(root, "br")
        child.tail = "tail"
        root.append(child)
        result = self.compare(root)
        self.assertEqual(result[-1].tail, "\n\ntail")
        root = Element("div")
        parent = root
        for _ in range(102):
            parent = SubElement(parent, "div")
        self.compare(root)

    def test_unsupported_values_preserve_partial_error_mutations(self) -> None:
        for field in ["text", "tail", "tag"]:
            for value in ["\ud800", 42, b"bytes"]:
                root = Element("div")
                SubElement(root, "p")
                last = SubElement(root, "br")
                setattr(last, field, value)
                self.compare(root)

        class CustomElement(Element):
            pass

        self.compare(CustomElement("div"))

    def check_custom_callbacks(self, use_python: bool) -> None:
        root = Element("div")
        SubElement(root, "p")
        md = Markdown()
        processor = PythonPrettifyTreeprocessor(md) if use_python else PrettifyTreeprocessor(md)
        calls = []

        def is_block_level(tag: str) -> bool:
            calls.append((tag, root.text))
            return tag != "p"

        with mock.patch.object(md, "is_block_level", side_effect=is_block_level):
            processor.run(root)
        self.assertEqual(calls, [("div", None), ("p", None), ("p", None)])
        with mock.patch.object(processor, "_prettifyETree") as callback:
            processor.run(root)
            callback.assert_called_once_with(root)

    def test_custom_callbacks(self) -> None:
        self.check_custom_callbacks(True)
        self.check_custom_callbacks(False)
        md = Markdown()
        md.block_level_elements = cast(list[str], ("div", "p"))
        root = Element("div")
        SubElement(root, "p")
        PrettifyTreeprocessor(md).run(root)
        self.assertEqual(root.text, "\n")

    def test_generated_trees(self) -> None:
        rng = random.Random(19)
        values = [None, "", "\n", "\t", "\u001c", "\u200b", "فارسی ", "x\n", AtomicString(" ")]
        tags = ["div", "p", "span", "br", "pre", "code", "li", "ul", "PRE", "p/"]
        for _ in range(500):
            root = Element(rng.choice(tags))
            nodes = [root]
            for _ in range(rng.randrange(12)):
                nodes.append(SubElement(rng.choice(nodes), rng.choice(tags)))
            for node in nodes:
                node.text = rng.choice(values)
                node.tail = rng.choice(values)
            self.compare(root)

    def test_rust_prettify_is_used(self) -> None:
        if prettify._rust_prettify is None:
            self.skipTest("Rust prettify is disabled")
        root = Element("div")
        SubElement(root, "br").tail = "فارسی"
        SubElement(SubElement(root, "pre"), "code").text = "code \n\n"
        with mock.patch.object(PythonPrettifyTreeprocessor, "run", side_effect=AssertionError):
            PrettifyTreeprocessor(Markdown()).run(root)
        self.assertEqual(root[0].tail, "\nفارسی")
        self.assertEqual(root[1][0].text, "code\n")
        self.assertIs(type(root[1][0].text), AtomicString)
