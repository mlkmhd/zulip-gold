"""Opt-in formatting of unread state already selected by Django."""

from __future__ import annotations

import os
from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from zerver.lib.event_types import MessageDetails
    from zerver.lib.message import RawUnreadMessagesResult, UnreadMessagesResult

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_UNREAD_PAYLOAD") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def aggregate(
    raw: RawUnreadMessagesResult, allow_empty_topic: bool, fallback: str
) -> UnreadMessagesResult | None:
    native = getattr(_backend, "aggregate_unread_payload", None)
    if native is None:
        return None
    return cast("UnreadMessagesResult | None", native(raw, allow_empty_topic, fallback))


def format_details(
    my_user_id: int, raw: RawUnreadMessagesResult, details_model: type[MessageDetails]
) -> dict[str, MessageDetails] | None:
    native = getattr(_backend, "format_unread_details", None)
    if native is None:
        return None
    return cast("dict[str, MessageDetails] | None", native(my_user_id, raw, details_model))
