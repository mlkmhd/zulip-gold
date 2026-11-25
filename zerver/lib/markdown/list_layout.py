import re
from xml.etree.ElementTree import Element, SubElement

from markdown.blockparser import State
from markdown.blockprocessors import OListProcessor
from markdown.util import AtomicString

from zerver.lib.native_backend import load_native_contract

_rust_list_layout = load_native_contract(
    "ZULIP_RUST_MARKDOWN_LIST_LAYOUT", "MARKDOWN_LIST_LAYOUT_API"
)

_STOCK_CHILD = {
    "ol": re.compile(r"^[ ]{0,1}((\d+\.))[ ]+(.*)"),
    "ul": re.compile(r"^[ ]{0,1}(([*+-]))[ ]+(.*)"),
}
_STOCK_INDENT = re.compile(r"^[ ]{2,3}((\d+\.)|[*+-])[ ]+.*")


def run_list_layout(processor: OListProcessor, parent: Element, blocks: list[str]) -> bool:
    if (
        _rust_list_layout is None
        or type(parent) is not Element
        or type(parent.tag) is not str
        or type(blocks) is not list
        or not blocks
        or type(blocks[0]) is not str
        or type(processor.parser.state) is not State
        or type(processor.tab_length) is not int
        or processor.tab_length != 2
        or type(processor.TAG) is not str
        or processor.TAG not in _STOCK_CHILD
        or type(processor.SIBLING_TAGS) is not list
        or any(type(tag) is not str for tag in processor.SIBLING_TAGS)
        or processor.SIBLING_TAGS != [processor.TAG]
        or type(processor.LAZY_OL) is not bool
        or (processor.TAG == "ul" and not processor.LAZY_OL)
        or type(processor.CHILD_RE) is not re.Pattern
        or _STOCK_CHILD[processor.TAG] != processor.CHILD_RE
        or type(processor.INDENT_RE) is not re.Pattern
        or processor.INDENT_RE != _STOCK_INDENT
    ):
        return False
    sibling = processor.lastChild(parent)
    if sibling is not None and (type(sibling) is not Element or type(sibling.tag) is not str):
        return False
    previous = (
        sibling[-1]
        if sibling is not None and sibling.tag == processor.TAG and len(sibling)
        else None
    )
    if previous is not None and type(previous) is not Element:
        return False
    last_child = processor.lastChild(previous) if previous is not None else None
    if last_child is not None and type(last_child) is not Element:
        return False
    previous_text = previous.text if previous is not None else None
    tail = last_child.tail if last_child is not None else None
    if any(
        value is not None and type(value) not in (str, AtomicString)
        for value in (previous_text, tail)
    ):
        return False
    result = _rust_list_layout.plan_markdown_list(
        blocks[0],
        processor.TAG == "ol",
        processor.LAZY_OL,
        parent.tag,
        sibling.tag if sibling is not None else None,
        previous_text,
        tail,
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
