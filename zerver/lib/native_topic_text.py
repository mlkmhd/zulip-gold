"""Native format operations on already selected topic title text."""

from collections.abc import Callable
from typing import cast

from zerver.lib.native_backend import load_native_contract

_backend = load_native_contract("ZULIP_RUST_TOPIC_TEXT", "TOPIC_TITLE_API")


def _optional_symbol(name: str) -> object:
    symbol = getattr(_backend, name, None)
    return symbol if callable(symbol) else None


truncate_topic_title = cast(
    "Callable[[str, int], str | None] | None",
    _optional_symbol("truncate_topic_title"),
)
split_resolved_topic_title = cast(
    "Callable[[str], tuple[bool, str] | None] | None",
    _optional_symbol("split_resolved_topic_title"),
)
