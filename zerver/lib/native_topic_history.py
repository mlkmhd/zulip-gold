"""Native projection of topic history rows already selected by Django."""

import os
from importlib import import_module
from types import ModuleType
from typing import TypedDict, cast

from zerver.lib.native_backend import supports_default_rust_backend


class TopicHistoryEntry(TypedDict):
    name: str
    max_id: int


def _load_topic_history_backend() -> ModuleType | None:
    requested = os.environ.get("ZULIP_RUST_TOPIC_HISTORY") == "1"
    automatic = supports_default_rust_backend()
    if not automatic and not requested:
        return None
    try:
        backend = import_module("_zulip_backend")
    except ImportError as error:
        if requested and (
            not isinstance(error, ModuleNotFoundError) or error.name != "_zulip_backend"
        ):
            raise
        return None
    if requested or getattr(backend, "VISIBLE_TOPIC_HISTORY_API", None) == 1:
        return backend
    return None


_backend = _load_topic_history_backend()


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
