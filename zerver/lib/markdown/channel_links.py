import os
from importlib import import_module
from urllib import parse as urlparse
from xml.etree.ElementTree import Element, SubElement

from markdown.util import AtomicString

from zerver.lib.native_backend import load_native_contract

_rust_channel_spans = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_CHANNEL_SPANS") == "1"
    else None
)


_prepared_backend = load_native_contract(
    "ZULIP_RUST_PREPARED_CHANNEL_LINK_FIELDS", "PREPARED_CHANNEL_LINK_FIELDS_API"
)


def _standard_encoders() -> bool:
    from zerver.lib import markdown, url_encoding

    encoders = markdown._native_channel_callbacks
    quoting = markdown._native_channel_url_callbacks
    replacements = url_encoding.hash_replacements
    return (
        markdown.encode_channel is encoders[4]
        and markdown.encode_hash_component is encoders[5]
        and url_encoding.encode_hash_component is encoders[5]
        and urlparse.quote is quoting[0]
        and urlparse.quote_from_bytes is quoting[1]
        and getattr(url_encoding._rust_url_fragments, "encode_narrow_hash_component", None)
        is quoting[2]
        and type(replacements) is dict
        and all(type(key) is str and type(value) is str for key, value in replacements.items())
        and replacements == markdown._native_channel_url_replacements
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
    if _rust_channel_spans is not None:
        native = _rust_channel_spans.markdown_channel_link_fields
    else:
        native = getattr(_prepared_backend, "markdown_channel_link_fields", None)
        if (
            native is None
            or type(channel_id) is not int
            or type(channel_name) is not str
            or type(empty_topic_name) is not str
            or (topic_name is not None and type(topic_name) is not str)
            or (with_operand is not None and type(with_operand) is not int)
            or (
                message_id is not None
                and (type(message_id) is not str or not message_id.isdecimal())
            )
            or (topic_name is None and (message_id is not None or with_operand is not None))
            or (message_id is not None and with_operand is not None)
            or not _standard_encoders()
        ):
            return None
    result = native(channel_id, channel_name, topic_name, with_operand, message_id)
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
