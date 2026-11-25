"""Formatting of search hits after Django has selected them."""

from collections.abc import Iterable
from typing import TypeAlias, cast

from zerver.lib.native_backend import load_native_contract

SearchHighlightRow: TypeAlias = tuple[
    int, str, str, Iterable[tuple[int, int]], Iterable[tuple[int, int]]
]

_backend = load_native_contract("ZULIP_RUST_SEARCH_HIGHLIGHTS", "SEARCH_HIGHLIGHTS_API")


def available() -> bool:
    return callable(getattr(_backend, "assemble_search_highlights", None))


def assemble(rows: list[SearchHighlightRow]) -> dict[int, dict[str, str]] | None:
    native = getattr(_backend, "assemble_search_highlights", None)
    if not callable(native):
        return None
    return cast("dict[int, dict[str, str]] | None", native(rows))
