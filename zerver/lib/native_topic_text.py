"""Optional format operations on already selected topic title text."""

from collections.abc import Callable
from importlib import import_module
from os import environ
from typing import cast

try:
    _backend = (
        import_module("_zulip_backend") if environ.get("ZULIP_RUST_TOPIC_TEXT") == "1" else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


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
