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


try:
    _private_arrival_backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_PRIVATE_UNREAD_ARRIVAL") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _private_arrival_backend = None


def has_private_arrival_planner() -> bool:
    return callable(getattr(_private_arrival_backend, "plan_private_unread_arrival", None))


def plan_private_arrival(
    my_user_id: int, display_recipient: object
) -> tuple[int | None, str | None] | None:
    native = getattr(_private_arrival_backend, "plan_private_unread_arrival", None)
    if not callable(native) or type(my_user_id) is not int or type(display_recipient) is not list:
        return None
    ids = []
    for row in display_recipient:
        if type(row) is not dict or any(type(key) is not str for key in row):
            return None
        user_id = row.get("id")
        if type(user_id) is not int:
            return None
        ids.append(user_id)
    # Only IDs cross. Do not give the extension recipient rows or unread state.
    result = native(my_user_id, ids)
    if type(result) is not tuple or len(result) != 2:
        return None
    other, group = result
    if (type(other) is int and group is None) or (other is None and type(group) is str):
        return other, group
    return None


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
