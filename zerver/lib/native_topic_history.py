"""Native projection of topic history rows already selected by Django."""

from typing import TypedDict, cast

from zerver.lib.native_backend import load_native_contract


class TopicHistoryEntry(TypedDict):
    name: str
    max_id: int


_backend = load_native_contract("ZULIP_RUST_TOPIC_HISTORY", "VISIBLE_TOPIC_HISTORY_API")


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
