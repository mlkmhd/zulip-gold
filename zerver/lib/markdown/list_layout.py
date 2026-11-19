import os
from importlib import import_module
from xml.etree.ElementTree import Element, SubElement

from markdown.blockprocessors import OListProcessor

_rust_list_layout = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_LIST_LAYOUT") == "1"
    else None
)


def run_list_layout(processor: OListProcessor, parent: Element, blocks: list[str]) -> bool:
    if (
        _rust_list_layout is None
        or not blocks
        or processor.tab_length != 2
        or processor.TAG not in {"ol", "ul"}
        or processor.SIBLING_TAGS != [processor.TAG]
        or (processor.TAG == "ul" and not processor.LAZY_OL)
    ):
        return False
    sibling = processor.lastChild(parent)
    previous = (
        sibling[-1]
        if sibling is not None and sibling.tag == processor.TAG and len(sibling)
        else None
    )
    last_child = processor.lastChild(previous) if previous is not None else None
    result = _rust_list_layout.plan_markdown_list(
        blocks[0],
        processor.TAG == "ol",
        processor.LAZY_OL,
        parent.tag,
        sibling.tag if sibling is not None else None,
        previous.text if previous is not None else None,
        last_child.tail if last_child is not None else None,
    )
    if result is None:
        return False
    target, first, items, number, start, wrap_text, tail = result
    blocks.pop(0)
    if number is not None:
        processor.STARTSWITH = number  # type: ignore[misc] # Upstream mutates this ClassVar-stubbed attribute.
    if target == "sibling":
        assert sibling is not None
        lst = sibling
        previous = lst[-1]
        if wrap_text:
            paragraph = Element("p")
            paragraph.text = previous.text
            previous.text = ""
            previous.insert(0, paragraph)
        if tail is not None:
            assert last_child is not None
            SubElement(previous, "p").text = tail
            last_child.tail = ""
        li = SubElement(lst, "li")
        processor.parser.state.set("looselist")
        assert first is not None
        processor.parser.parseBlocks(li, [first])
        processor.parser.state.reset()
    elif target == "parent":
        lst = parent
    else:
        lst = SubElement(parent, processor.TAG)
        if start is not None:
            lst.set("start", start)
    processor.parser.state.set("list")
    for item, indented in items:
        li = lst[-1] if indented else SubElement(lst, "li")
        processor.parser.parseBlocks(li, [item])
    processor.parser.state.reset()
    return True
