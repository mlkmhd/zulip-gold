import random
from typing import SupportsIndex, cast
from unittest import TestCase, mock
from xml.etree.ElementTree import (
    HTML_EMPTY,
    Comment,
    Element,
    ProcessingInstruction,
    QName,
    SubElement,
)

from markdown import Markdown
from markdown.serializers import to_html_string as python_to_html_string
from markdown.util import AtomicString
from typing_extensions import override

from zerver.lib.markdown import ZulipMarkdown, serialization


class MarkdownSerializationTest(TestCase):
    def assert_serialization(self, root: Element) -> None:
        try:
            expected = python_to_html_string(root)
        except (TypeError, ValueError, AttributeError, RecursionError) as error:
            with self.assertRaises(type(error)) as caught:
                serialization.to_html_string(root)
            self.assertEqual(str(caught.exception), str(error))
        else:
            self.assertEqual(serialization.to_html_string(root), expected)

    def test_entities_attributes_and_atomic_text(self) -> None:
        root = Element("p", {"z": '"\n<&>', "checked": "checked", "a": "a", "é": "فارسی"})
        root.text = AtomicString('<&bogus; &#12; &#Xff; &İ; &ı; &ſ; &K; &é; &#; &#x; &; "')
        root.tail = AtomicString("<&>\n")
        self.assertEqual(
            serialization.to_html_string(root),
            '<p a checked z="&quot;\n&lt;&amp;&gt;" é="فارسی">'
            '&lt;&bogus; &#12; &#Xff; &İ; &ı; &ſ; &K; &amp;é; &amp;#; &amp;#x; &amp;; "'
            "</p>&lt;&amp;&gt;\n",
        )

    def test_raw_text_empty_elements_and_children(self) -> None:
        for tag in [*HTML_EMPTY, "linK", "p", "", "script", "style", "scrİpt"]:
            for case_tag in [tag, tag.upper()]:
                root = Element(case_tag)
                root.text = "<raw &>"
                SubElement(root, "b").text = "<child &>"
                root[0].tail = "<tail &>"
                root.tail = "<root tail &>"
                self.assert_serialization(root)

    def test_comments_instructions_and_tagless_elements(self) -> None:
        # ElementTree supports tagless containers despite its type annotation.
        root = Element(cast(str, None))
        root.text = "<&>"
        root.set("ignored", "<&>")
        for node in [Comment("<&>"), ProcessingInstruction("target", "<&>")]:
            node.tail = "<&tail;>"
            node.set("ignored", "<&>")
            SubElement(node, "ignored").text = "not serialized"
            root.append(node)
        SubElement(root, "span").text = "فارسی"
        self.assertEqual(
            serialization.to_html_string(root),
            "&lt;&amp;&gt;<!--&lt;&amp;&gt;-->&lt;&tail;&gt;"
            "<?target &lt;&amp;&gt;?>&lt;&tail;&gt;<span>فارسی</span>",
        )

    def test_empty_elements_follow_the_python_runtime(self) -> None:
        root = Element("source")
        root.text = "text"
        for empty_tags, expected in [
            ({"source"}, "<source>text"),
            (set(), "<source>text</source>"),
        ]:
            with (
                mock.patch("markdown.serializers.HTML_EMPTY", empty_tags),
                mock.patch.object(serialization, "HTML_EMPTY", empty_tags),
            ):
                self.assertEqual(serialization.to_html_string(root), expected)
                self.assert_serialization(root)

    def test_generated_trees(self) -> None:
        rng = random.Random(7)
        tags = ["p", "span", "script", "STYLE", "BR", "linK", "wbr", "فارسی", ""]
        texts = [None, "", '<&>"\n', "&amp; &bogus; &#xA;", AtomicString("عنوان"), "\x02x\x03"]
        for _ in range(1000):
            root = Element("div")
            nodes = [root]
            for _ in range(rng.randrange(20)):
                node = SubElement(rng.choice(nodes), rng.choice(tags))
                node.text = rng.choice(texts)
                node.tail = rng.choice(texts)
                for key in rng.sample(["id", "class", "checked", "عنوان", "a&"], rng.randrange(5)):
                    node.set(key, rng.choice([key, "", '"<&>\n', "&bogus;", "فارسی"]))
                nodes.append(node)
            self.assert_serialization(root)

    def test_namespaces_and_nonstandard_values(self) -> None:
        for tag in [QName("urn:<&>\n", "p"), QName("plain"), QName("{broken"), 3, b"p"]:
            # These deliberately nonstandard values exercise the reference errors.
            root = Element(cast(str, tag))
            root.text = "<&>"
            self.assert_serialization(root)
        for key, value in [(QName("id"), QName("<&>")), ("id", 3), (3, "x"), ("id", None)]:
            root = Element("p")
            root.set(cast(str, key), cast(str, value))
            self.assert_serialization(root)
        for value in [None, 0, 3, b"text", ["text"], "\ud800"]:
            nodes = [
                Element("p"),
                cast(Element, Comment()),
                cast(Element, ProcessingInstruction("target")),
            ]
            for node in nodes:
                node.text = cast(str, value)
                self.assert_serialization(node)
                node.text = "text"
                node.tail = cast(str, value)
                self.assert_serialization(node)

    def test_surrogates_and_custom_types(self) -> None:
        class CustomElement(Element):
            pass

        class CustomString(str):
            __slots__ = ()

            @override
            def replace(self, old: str, new: str, count: SupportsIndex = -1) -> str:
                return "custom"

        roots: list[Element] = [
            CustomElement("p"),
            Element(CustomString("p")),
            Element("\ud800"),
            Element("p", {"\ud800": "x"}),
            Element("p", {"x": "\ud800"}),
            Element("p", {"x": CustomString("<&>")}),
        ]
        for root in roots:
            root.text = CustomString("<&>") if isinstance(root, CustomElement) else "text"
            self.assert_serialization(root)
        root = Element("p")
        root.text = CustomString("<&>")
        self.assert_serialization(root)
        root.text = "text"
        root.tail = CustomString("<&>")
        self.assert_serialization(root)

    def test_deep_and_cyclic_trees(self) -> None:
        root = Element("div")
        child = root
        for _ in range(110):
            child = SubElement(child, "div")
        self.assert_serialization(root)
        child.append(root)
        self.assert_serialization(root)

    def test_output_formats(self) -> None:
        engine = ZulipMarkdown([], 0, False)
        self.assertIs(engine.serializer, serialization.to_html_string)
        engine.set_output_format("xhtml")
        self.assertIs(engine.serializer, Markdown.output_formats["xhtml"])
        # Python-Markdown accepts case-insensitive numbered aliases.
        engine.set_output_format("HTML5")  # type: ignore[arg-type]
        self.assertIs(engine.serializer, serialization.to_html_string)
        self.assertIs(Markdown.output_formats["html"], python_to_html_string)

    def test_rust_serialization_is_used(self) -> None:
        if serialization._rust_serialization is None:
            self.skipTest("Rust serialization is disabled")
        with mock.patch.object(serialization, "python_to_html_string", side_effect=AssertionError):
            self.test_entities_attributes_and_atomic_text()
            self.test_raw_text_empty_elements_and_children()
            self.test_comments_instructions_and_tagless_elements()
            self.test_empty_elements_follow_the_python_runtime()
            self.test_generated_trees()

    def test_fallback_preserves_tree(self) -> None:
        if serialization._rust_serialization is None:
            self.skipTest("Rust serialization is disabled")
        root = Element("p")
        SubElement(root, "b").text = AtomicString("before")
        SubElement(root, "i").text = "\ud800"
        nodes = list(root.iter())
        before = [(node.tag, node.text, node.tail, node.items()) for node in nodes]
        with mock.patch.object(
            serialization, "python_to_html_string", wraps=python_to_html_string
        ) as fallback:
            self.assert_serialization(root)
            fallback.assert_called_once_with(root)
        self.assertEqual(list(root.iter()), nodes)
        self.assertEqual([(node.tag, node.text, node.tail, node.items()) for node in nodes], before)
        self.assertIsInstance(root[0].text, AtomicString)
