import os
from importlib import import_module
from xml.etree.ElementTree import Element, SubElement

from markdown.util import AtomicString

_rust_channel_spans = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_CHANNEL_SPANS") == "1"
    else None
)


def channel_link(
    channel_id: int,
    channel_name: str,
    *,
    topic_name: str | None = None,
    with_operand: int | None = None,
    message_id: str | None = None,
    empty_topic_name: str = "",
) -> Element | None:
    if _rust_channel_spans is None:
        return None
    result = _rust_channel_spans.markdown_channel_link_fields(
        channel_id, channel_name, topic_name, with_operand, message_id
    )
    if result is None:
        return None
    attributes, text, empty_topic, tail = result
    element = Element("a", dict(attributes))
    element.text = AtomicString(text)
    if empty_topic:
        topic = SubElement(element, "em")
        topic.text = empty_topic_name
        if tail is not None:
            topic.tail = AtomicString(tail)
    return element
