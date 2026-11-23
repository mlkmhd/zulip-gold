"""Optional masks for receipt recipients already authorized by Django."""

import os
from collections.abc import Set as AbstractSet
from importlib import import_module
from typing import cast

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


def available() -> bool:
    return callable(getattr(_backend, "plan_new_user_message_masks", None))


def plan(
    eligible: list[int],
    base_flags: int,
    mark_as_read: AbstractSet[int],
    mentions: AbstractSet[int],
    alerts: AbstractSet[int],
    topics: AbstractSet[int],
    limit_unread: AbstractSet[int] | None,
) -> list[int] | None:
    native = getattr(_backend, "plan_new_user_message_masks", None)
    if not callable(native):
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
