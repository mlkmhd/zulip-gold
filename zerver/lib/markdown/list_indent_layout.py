from xml.etree.ElementTree import Element

from markdown.blockparser import State
from markdown.blockprocessors import ListIndentProcessor
from markdown.util import AtomicString

from zerver.lib.native_backend import load_native_contract

_rust_list_indent_layout = load_native_contract(
    "ZULIP_RUST_MARKDOWN_LIST_INDENT_LAYOUT", "MARKDOWN_LIST_INDENT_LAYOUT_API"
)


def run_list_indent_layout(
    processor: ListIndentProcessor, parent: Element, blocks: list[str]
) -> bool:
    if (
        _rust_list_indent_layout is None
        or type(parent) is not Element
        or type(blocks) is not list
        or not blocks
        or type(blocks[0]) is not str
        or type(processor.parser.state) is not State
        or type(processor.tab_length) is not int
        or processor.tab_length != 2
        or type(processor.ITEM_TYPES) is not list
        or type(processor.LIST_TYPES) is not list
        or any(type(tag) is not str for tag in processor.ITEM_TYPES)
        or any(type(tag) is not str for tag in processor.LIST_TYPES)
        or processor.ITEM_TYPES != ["li"]
        or processor.LIST_TYPES != ["ul", "ol"]
    ):
        return False
    descendants = [parent]
    while len(descendants[-1]):
        descendants.append(descendants[-1][-1])
    if any(
        type(node) is not Element
        or type(node.tag) is not str
        or (node.text is not None and type(node.text) not in (str, AtomicString))
        for node in descendants
    ):
        return False
    result = _rust_list_indent_layout.plan_markdown_list_indentation(
        blocks[0],
        processor.parser.state.isstate("list"),
        [(node.tag, bool(node.text)) for node in descendants],
    )
    if result is None:
        return False
    index, method, wrap_text, text = result
    target = descendants[index]
    blocks.pop(0)
    processor.parser.state.set("detabbed")
    if wrap_text:
        paragraph = Element("p")
        paragraph.text = target.text
        target.text = ""
        target.insert(0, paragraph)
    if method == "chunk":
        processor.parser.parseChunk(target, text)
    elif method == "new":
        processor.create_item(target, text)
    else:
        processor.parser.parseBlocks(target, [text])
    processor.parser.state.reset()
    return True
