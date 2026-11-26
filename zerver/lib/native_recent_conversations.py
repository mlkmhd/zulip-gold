"""Opt-in shaping of recent conversation rows already selected by Django."""

from __future__ import annotations

import os
from importlib import import_module
from typing import TYPE_CHECKING, TypedDict, cast

from zerver.lib.native_backend import load_native_contract

if TYPE_CHECKING:
    from zerver.lib.message import RecentPrivateConversationDict


class RecentConversationPayload(TypedDict):
    user_ids: list[int]
    max_message_id: int
    pinned: bool


_finalize_backend = load_native_contract("ZULIP_RUST_RECENT_DM_RESPONSE", "RECENT_DM_RESPONSE_API")

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_RECENT_CONVERSATIONS") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def assembly_available() -> bool:
    return getattr(_backend, "assemble_recent_conversations", None) is not None


def assemble(
    me: int, recent: dict[int, int], subscriptions: list[tuple[int, int, bool]]
) -> dict[frozenset[int], RecentPrivateConversationDict] | None:
    native = getattr(_backend, "assemble_recent_conversations", None)
    if native is None:
        return None
    return cast(
        "dict[frozenset[int], RecentPrivateConversationDict] | None",
        native(me, recent, subscriptions),
    )


def finalize(
    conversations: dict[frozenset[int], RecentPrivateConversationDict],
) -> list[RecentConversationPayload] | None:
    backend = _finalize_backend or _backend
    native = getattr(backend, "finalize_recent_conversations", None)
    if native is None:
        return None
    return cast("list[RecentConversationPayload] | None", native(conversations))
