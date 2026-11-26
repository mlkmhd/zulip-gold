"""Native masks for receipt recipients already authorized by Django."""

import os
from collections.abc import Set as AbstractSet
from importlib import import_module
from typing import cast

from zerver.lib.native_backend import load_native_contract
from zerver.models import UserMessage

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_MESSAGE_SEND_MASKS") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


_prepared_backend = load_native_contract(
    "ZULIP_RUST_PREPARED_SEND_RECEIPT_MASKS", "PREPARED_SEND_RECEIPT_MASKS_API"
)


def _get_planner() -> object:
    backend = _backend if _backend is not None else _prepared_backend
    return getattr(backend, "plan_new_user_message_masks", None)


def available() -> bool:
    return callable(_get_planner())


def plan(
    eligible: list[int],
    base_flags: int,
    mark_as_read: AbstractSet[int],
    mentions: AbstractSet[int],
    alerts: AbstractSet[int],
    topics: AbstractSet[int],
    limit_unread: AbstractSet[int] | None,
) -> list[int] | None:
    native = _get_planner()
    if not callable(native):
        return None
    if _backend is None and (
        type(eligible) is not list
        or type(base_flags) is not int
        or not 0 <= base_flags < 2**64
        or any(
            type(ids) not in (set, frozenset) for ids in (mark_as_read, mentions, alerts, topics)
        )
        or (limit_unread is not None and type(limit_unread) not in (set, frozenset))
    ):
        return None
    flags = UserMessage.flags
    return cast(
        "list[int] | None",
        native(
            eligible,
            base_flags,
            (
                flags.read.mask,
                flags.mentioned.mask,
                flags.has_alert_word.mask,
                flags.topic_wildcard_mentioned.mask,
            ),
            (mark_as_read, mentions, alerts, topics),
            limit_unread,
        ),
    )
