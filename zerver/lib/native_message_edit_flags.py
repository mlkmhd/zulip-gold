"""Optional calculation of rendering-derived receipt flag changes."""

import os
from collections.abc import Set as AbstractSet
from importlib import import_module
from typing import cast

from zerver.models import UserMessage

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_MESSAGE_EDIT_FLAGS") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def available() -> bool:
    return callable(getattr(_backend, "plan_user_message_flag_deltas", None))


def plan(
    rows: list[UserMessage],
    alerts: AbstractSet[int],
    mentions: AbstractSet[int],
    topics: AbstractSet[int],
    stream_mention: bool,
) -> list[tuple[int, int]] | None:
    native = getattr(_backend, "plan_user_message_flag_deltas", None)
    if not callable(native) or any(type(row) is not UserMessage for row in rows):
        return None
    flags = UserMessage.flags
    masks = (
        flags.has_alert_word.mask,
        flags.mentioned.mask,
        flags.stream_wildcard_mentioned.mask,
        flags.topic_wildcard_mentioned.mask,
    )
    return cast(
        "list[tuple[int, int]] | None",
        native(
            [(row.user_profile_id, int(row.flags)) for row in rows],
            masks,
            (alerts, mentions, topics),
            stream_mention,
        ),
    )
