from xml.etree.ElementTree import Element, SubElement

from markdown import util
from markdown.blockprocessors import CodeBlockProcessor as PythonCodeBlockProcessor
from markdown.blockprocessors import EmptyBlockProcessor as PythonEmptyBlockProcessor
from markdown.util import AtomicString
from typing_extensions import override

from zerver.lib.native_backend import load_native_contract

_rust_code_blocks = load_native_contract(
    "ZULIP_RUST_MARKDOWN_CODE_BLOCKS", "MARKDOWN_CODE_BLOCKS_API"
)
_REFERENCE_CODE_ESCAPE = util.code_escape
_REFERENCE_CODE_DETAB = PythonCodeBlockProcessor.detab
_REFERENCE_CODE_LAST_CHILD = PythonCodeBlockProcessor.lastChild
_REFERENCE_EMPTY_LAST_CHILD = PythonEmptyBlockProcessor.lastChild


def _stock_block_shape(parent: Element, blocks: list[str]) -> bool:
    return (
        type(parent) is Element and type(blocks) is list and bool(blocks) and type(blocks[0]) is str
    )


class CodeBlockProcessor(PythonCodeBlockProcessor):
    @override
    def run(self, parent: Element, blocks: list[str]) -> None:
        if (
            _rust_code_blocks is None
            or type(self) is not CodeBlockProcessor
            or not _stock_block_shape(parent, blocks)
            or type(self.tab_length) is not int
            or util.code_escape is not _REFERENCE_CODE_ESCAPE
            or getattr(self.detab, "__func__", None) is not _REFERENCE_CODE_DETAB
            or getattr(self.lastChild, "__func__", None) is not _REFERENCE_CODE_LAST_CHILD
        ):
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
        if previous is not None and type(previous) not in (str, AtomicString):
            super().run(parent, blocks)
            return
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
        if (
            _rust_code_blocks is None
            or type(self) is not EmptyBlockProcessor
            or not _stock_block_shape(parent, blocks)
            or getattr(self.lastChild, "__func__", None) is not _REFERENCE_EMPTY_LAST_CHILD
        ):
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
        if previous is not None and type(previous) not in (str, AtomicString):
            super().run(parent, blocks)
            return
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
