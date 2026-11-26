"""Native formatting of unread state already selected by Django."""

from __future__ import annotations

import os
from importlib import import_module
from typing import TYPE_CHECKING, cast

from zerver.lib.native_backend import load_native_contract

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


_aggregate_backend = load_native_contract("ZULIP_RUST_UNREAD_AGGREGATE", "UNREAD_AGGREGATE_API")


def aggregate(
    raw: RawUnreadMessagesResult, allow_empty_topic: bool, fallback: str
) -> UnreadMessagesResult | None:
    backend = _aggregate_backend if _aggregate_backend is not None else _backend
    native = getattr(backend, "aggregate_unread_payload", None)
    if native is None:
        return None
    return cast("UnreadMessagesResult | None", native(raw, allow_empty_topic, fallback))


_details_backend = load_native_contract("ZULIP_RUST_UNREAD_DETAILS", "UNREAD_DETAILS_API")


def format_details(
    my_user_id: int, raw: RawUnreadMessagesResult, details_model: type[MessageDetails]
) -> dict[str, MessageDetails] | None:
    backend = _details_backend if _details_backend is not None else _backend
    native = getattr(backend, "format_unread_details", None)
    if native is None:
        return None
    return cast("dict[str, MessageDetails] | None", native(my_user_id, raw, details_model))
