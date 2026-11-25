from xml.etree.ElementTree import HTML_EMPTY, Comment, Element, ProcessingInstruction

from markdown import serializers
from markdown.serializers import to_html_string as python_to_html_string
from markdown.util import AtomicString

from zerver.lib.native_backend import load_native_contract

_rust_serialization = load_native_contract(
    "ZULIP_RUST_MARKDOWN_SERIALIZATION", "MARKDOWN_SERIALIZATION_API"
)
_REFERENCE_HTML_SERIALIZER = python_to_html_string
_REFERENCE_HTML_IMPL = vars(serializers).get("_serialize_html")


def to_html_string(root: Element) -> str:
    if (
        _rust_serialization is not None
        and python_to_html_string is _REFERENCE_HTML_SERIALIZER
        and serializers.to_html_string is _REFERENCE_HTML_SERIALIZER
        and vars(serializers).get("_serialize_html") is _REFERENCE_HTML_IMPL
        and HTML_EMPTY is serializers.HTML_EMPTY
        and type(HTML_EMPTY) is set
        and all(type(tag) is str for tag in HTML_EMPTY)
    ):
        nodes = []
        pending = [(root, 0)]
        while pending:
            node, depth = pending.pop()
            if type(node) is not Element or depth > 100:
                break
            tag: object = node.tag
            attributes: list[tuple[str, str]] = []
            children = list(node)
            if tag is Comment or tag is ProcessingInstruction:
                kind = 2 if tag is Comment else 3
                tag = ""
                children = []
                if node.text is None:
                    break
            elif tag is None:
                kind = 1
                tag = ""
            elif type(tag) in (str, AtomicString):
                kind = 0
                attributes = list(node.items())
            else:
                break
            if any(
                value is not None and type(value) not in (str, AtomicString)
                for value in (node.text, node.tail)
            ) or any(
                type(value) not in (str, AtomicString) for pair in attributes for value in pair
            ):
                break
            nodes.append((kind, tag, node.text, node.tail, attributes, len(children)))
            pending.extend((child, depth + 1) for child in reversed(children))
        else:
            result = _rust_serialization.serialize_markdown_html(nodes, list(HTML_EMPTY))
            if result is not None:
                return result
    return python_to_html_string(root)
