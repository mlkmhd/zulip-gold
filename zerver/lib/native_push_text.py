"""Formatting of plaintext already prepared by Django for mobile push."""

from collections.abc import Callable
from typing import Literal, cast

import lxml.html

from zerver.lib.native_backend import load_native_contract
from zerver.lib.types import UserDisplayRecipient

_backend = load_native_contract("ZULIP_RUST_PUSH_TEXT", "MOBILE_PUSH_TEXT_API")
_ordered_list_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_ORDERED_LIST", "MOBILE_ORDERED_LIST_API"
)


_plain_preview_tree_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_PLAIN_PREVIEW_TREE", "MOBILE_PLAIN_PREVIEW_TREE_API"
)
_PLAIN_PREVIEW_TAGS = frozenset(
    {
        "div",
        "p",
        "br",
        "span",
        "a",
        "b",
        "strong",
        "em",
        "i",
        "u",
        "s",
        "del",
        "code",
        "pre",
        "li",
        "ul",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)
_MAX_PREVIEW_NODES = 512
_base_payload_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_BASE_PAYLOAD", "MOBILE_BASE_PAYLOAD_API"
)


_message_identity_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_MESSAGE_IDENTITY", "MOBILE_MESSAGE_IDENTITY_API"
)


def format_message_identity(
    payload: dict[str, object], message_id: int, mode: Literal["apns", "legacy", "modern"]
) -> bool:
    native = getattr(_message_identity_backend, "format_mobile_message_identity", None)
    return cast(bool, native(payload, message_id, mode)) if callable(native) else False


_android_message_envelope_backend = load_native_contract(
    "ZULIP_RUST_ANDROID_MESSAGE_ENVELOPE", "ANDROID_MESSAGE_ENVELOPE_API"
)


def format_android_message(
    payload: dict[str, object],
    timestamp: int,
    content: str,
    sender_name: str,
    sender_avatar_url: str,
) -> dict[str, str] | None:
    native = getattr(_android_message_envelope_backend, "format_prepared_android_message", None)
    if not callable(native):
        return None
    return cast(
        "dict[str, str]", native(payload, timestamp, content, sender_name, sender_avatar_url)
    )


_direct_recipients_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_DIRECT_RECIPIENTS", "MOBILE_DIRECT_RECIPIENTS_API"
)


def format_direct_recipient_ids(
    recipients: list[UserDisplayRecipient], get_sender_id: Callable[[], int]
) -> list[int] | None:
    native = getattr(_direct_recipients_backend, "format_mobile_direct_recipient_ids", None)
    return cast("list[int] | None", native(recipients, get_sender_id)) if callable(native) else None


_channel_recipient_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_CHANNEL_RECIPIENT", "MOBILE_CHANNEL_RECIPIENT_API"
)


def format_channel_recipient(
    payload: dict[str, object], channel_id: int, channel_name: str, legacy: bool
) -> bool:
    native = getattr(_channel_recipient_backend, "format_mobile_channel_recipient", None)
    if not callable(native) or type(legacy) is not bool:
        return False
    return cast(bool, native(payload, channel_id, channel_name, legacy))


_apns_alert_title_backend = load_native_contract(
    "ZULIP_RUST_APNS_ALERT_TITLE", "APNS_ALERT_TITLE_API"
)


def format_apns_group_title(names: list[str]) -> str | None:
    native = getattr(_apns_alert_title_backend, "format_apns_group_title", None)
    return cast("str | None", native(names)) if callable(native) else None


def format_apns_channel_title(channel: str, topic: str) -> str | None:
    native = getattr(_apns_alert_title_backend, "format_apns_channel_title", None)
    return cast("str | None", native(channel, topic)) if callable(native) else None


MobileBaseFormatter = Callable[[tuple[str, int, str] | None, str, str, int], dict[str, object]]


def get_base_payload_formatter() -> MobileBaseFormatter | None:
    native = getattr(_base_payload_backend, "format_mobile_base_payload", None)
    return cast("MobileBaseFormatter", native) if callable(native) else None


_remove_payload_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_REMOVE_PAYLOAD", "MOBILE_REMOVE_PAYLOAD_API"
)


def format_remove_fields(
    payload: dict[str, object], message_ids: list[int], for_legacy_clients: bool
) -> bool:
    native = getattr(_remove_payload_backend, "format_mobile_remove_fields", None)
    if not callable(native) or type(for_legacy_clients) is not bool:
        return False
    return cast(bool, native(payload, message_ids, for_legacy_clients))


def render_plain_preview_tree(element: lxml.html.HtmlElement) -> str | None:
    native = getattr(_plain_preview_tree_backend, "render_plain_mobile_preview_tree", None)
    if not callable(native):
        return None
    rows: list[tuple[int, str, str]] = []
    pending = [(0, element)]
    while pending:
        depth, node = pending.pop()
        if type(node) is not lxml.html.HtmlElement or node.tag not in _PLAIN_PREVIEW_TAGS:
            return None
        classes = node.get("class", "")
        if "emoji" in classes or "spoiler-block" in classes:
            return None
        if depth > 128 or len(rows) + len(pending) + len(node) >= _MAX_PREVIEW_NODES:
            return None
        rows.append((depth, node.text or "", node.tail or ""))
        pending.extend((depth + 1, child) for child in reversed(node))
    return cast("str | None", native(rows))


_quote_collapse_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_QUOTE_COLLAPSE", "MOBILE_QUOTE_COLLAPSE_API"
)


def plan_quote_collapse(statuses: list[bool]) -> list[bool | None] | None:
    native = getattr(_quote_collapse_backend, "plan_mobile_quote_collapse", None)
    return cast("list[bool | None] | None", native(statuses)) if callable(native) else None


_spoiler_header_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_SPOILER_HEADER", "MOBILE_SPOILER_HEADER_API"
)


def format_spoiler_header(text: str) -> str | None:
    native = getattr(_spoiler_header_backend, "format_mobile_spoiler_header", None)
    return cast("str | None", native(text)) if callable(native) else None


OrderedListFormatter = Callable[[list[str], int, int], str | None]


def get_ordered_list_formatter() -> OrderedListFormatter | None:
    native = getattr(_ordered_list_backend, "format_mobile_ordered_list", None)
    return cast("OrderedListFormatter", native) if callable(native) else None


def quote(text: str) -> str | None:
    if _backend is None:
        return None
    native = getattr(_backend, "quote_mobile_push_text", None)
    return cast("str | None", native(text)) if callable(native) else None


def truncate(text: str) -> tuple[str, bool] | None:
    if _backend is None:
        return None
    native = getattr(_backend, "truncate_mobile_push_text", None)
    return cast("tuple[str, bool] | None", native(text)) if callable(native) else None
