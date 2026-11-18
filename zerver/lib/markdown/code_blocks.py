import os
from importlib import import_module
from xml.etree.ElementTree import Element, SubElement

from markdown.blockprocessors import CodeBlockProcessor as PythonCodeBlockProcessor
from markdown.blockprocessors import EmptyBlockProcessor as PythonEmptyBlockProcessor
from markdown.util import AtomicString
from typing_extensions import override

_rust_code_blocks = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_CODE_BLOCKS") == "1"
    else None
)


class CodeBlockProcessor(PythonCodeBlockProcessor):
    @override
    def run(self, parent: Element, blocks: list[str]) -> None:
        if _rust_code_blocks is None:
            super().run(parent, blocks)
            return
        sibling = self.lastChild(parent)
        code = (
            sibling[0]
            if sibling is not None
            and sibling.tag == "pre"
            and len(sibling) > 0
            and sibling[0].tag == "code"
            else None
        )
        previous = code.text if code is not None else None
        result = _rust_code_blocks.plan_markdown_code_block(
            blocks[0], self.tab_length, code is not None, previous
        )
        if result is None:
            super().run(parent, blocks)
            return
        text, remainder = result
        blocks.pop(0)
        if code is None:
            code = SubElement(SubElement(parent, "pre"), "code")
        code.text = AtomicString(text)
        if remainder:
            blocks.insert(0, remainder)


class EmptyBlockProcessor(PythonEmptyBlockProcessor):
    @override
    def run(self, parent: Element, blocks: list[str]) -> None:
        if _rust_code_blocks is None:
            super().run(parent, blocks)
            return
        sibling = self.lastChild(parent)
        code = (
            sibling[0]
            if sibling is not None
            and sibling.tag == "pre"
            and len(sibling) > 0
            and sibling[0].tag == "code"
            else None
        )
        previous = code.text if code is not None else None
        result = _rust_code_blocks.plan_markdown_empty_block(blocks[0], code is not None, previous)
        if result is None:
            super().run(parent, blocks)
            return
        text, remainder = result
        blocks.pop(0)
        if remainder:
            blocks.insert(0, remainder)
        if text is not None:
            assert code is not None
            code.text = AtomicString(text)
