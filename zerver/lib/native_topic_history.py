"""Optional projection of topic history rows already selected by Django."""

import os
from importlib import import_module
from typing import TypedDict, cast


class TopicHistoryEntry(TypedDict):
    name: str
    max_id: int


try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_TOPIC_HISTORY") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def format_visible_rows(
    rows: list[tuple[str, int]], allow_empty_topic_name: bool, empty_topic_fallback: str
) -> list[TopicHistoryEntry] | None:
    native = getattr(_backend, "format_visible_topic_history", None)
    if not callable(native):
        return None
    return cast(
        list[TopicHistoryEntry] | None,
        native(rows, allow_empty_topic_name, empty_topic_fallback),
    )
