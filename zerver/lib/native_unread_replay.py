"""Optional reconciliation of unread state already selected by Django."""

import os
from importlib import import_module
from typing import cast

from zerver.lib.topic import TOPIC_NAME

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_UNREAD_REPLAY") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def remove(raw: object, ids: object) -> bool:
    native = getattr(_backend, "remove_unread_messages", None)
    return callable(native) and cast(bool, native(raw, ids))


def restore(raw: object, user_id: int, details: object) -> bool:
    native = getattr(_backend, "restore_unread_messages", None)
    return callable(native) and cast(bool, native(raw, user_id, details))


def move(raw: object, event: dict[str, object]) -> bool:
    native = getattr(_backend, "move_unread_messages", None)
    if not callable(native) or type(event) is not dict:
        return False
    stream = event.get("new_stream_id")
    topic = event.get(TOPIC_NAME)
    if ("new_stream_id" in event and stream is None) or (TOPIC_NAME in event and topic is None):
        return False
    if stream is None and topic is None:
        return False
    # No message content, recipient records, or other event fields cross.
    return cast(bool, native(raw, event.get("message_ids"), stream, topic))
