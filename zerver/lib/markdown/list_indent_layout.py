import os
from importlib import import_module
from xml.etree.ElementTree import Element

from markdown.blockprocessors import ListIndentProcessor

_rust_list_indent_layout = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_LIST_INDENT_LAYOUT") == "1"
    else None
)


def run_list_indent_layout(
    processor: ListIndentProcessor, parent: Element, blocks: list[str]
) -> bool:
    if (
        _rust_list_indent_layout is None
        or not blocks
        or processor.tab_length != 2
        or processor.ITEM_TYPES != ["li"]
        or processor.LIST_TYPES != ["ul", "ol"]
    ):
        return False
    descendants = [parent]
    while len(descendants[-1]):
        descendants.append(descendants[-1][-1])
    if any(not isinstance(node.tag, str) for node in descendants):
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
