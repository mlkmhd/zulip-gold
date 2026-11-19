import os
from importlib import import_module
from xml.etree.ElementTree import Comment, Element, ProcessingInstruction

from markdown import Markdown
from markdown.treeprocessors import PrettifyTreeprocessor as PythonPrettifyTreeprocessor
from markdown.util import AtomicString
from typing_extensions import override

_rust_prettify = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_PRETTIFY") == "1"
    else None
)

_python_prettify = PythonPrettifyTreeprocessor._prettifyETree  # type: ignore[attr-defined] # Incomplete upstream stubs.
_python_is_block_level = Markdown.is_block_level


class PrettifyTreeprocessor(PythonPrettifyTreeprocessor):
    @override
    def run(self, root: Element) -> None:
        if (
            _rust_prettify is not None
            and getattr(self._prettifyETree, "__func__", None) is _python_prettify  # type: ignore[attr-defined] # Incomplete upstream stubs.
            and getattr(self.md.is_block_level, "__func__", None) is _python_is_block_level
            and type(self.md.block_level_elements) is list
            and all(type(tag) is str for tag in self.md.block_level_elements)
        ):
            nodes = []
            elements = []
            seen = set()
            pending = [(root, 0)]
            while pending:
                node, depth = pending.pop()
                if type(node) is not Element or depth > 100 or id(node) in seen:
                    break
                seen.add(id(node))
                tag: object = node.tag
                if tag is None or tag is Comment or tag is ProcessingInstruction:
                    tag = ""
                    block = False
                elif type(tag) in (str, AtomicString):
                    block = self.md.is_block_level(tag)
                else:
                    break
                if any(
                    value is not None and type(value) not in (str, AtomicString)
                    for value in (node.text, node.tail)
                ):
                    break
                nodes.append((tag, node.text, node.tail, block, len(node)))
                elements.append(node)
                pending.extend((child, depth + 1) for child in reversed(node))
            else:
                changes = _rust_prettify.prettify_markdown_tree(nodes)
                if changes is not None:
                    for index, text, atomic, value in changes:
                        if text:
                            elements[index].text = AtomicString(value) if atomic else value
                        else:
                            elements[index].tail = value
                    return
        super().run(root)
