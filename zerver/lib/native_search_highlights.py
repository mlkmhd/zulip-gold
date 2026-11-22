"""Optional formatting of search hits after Django has selected them."""

from collections.abc import Iterable
from importlib import import_module
from os import environ
from typing import TypeAlias, cast

SearchHighlightRow: TypeAlias = tuple[
    int, str, str, Iterable[tuple[int, int]], Iterable[tuple[int, int]]
]

try:
    _backend = (
        import_module("_zulip_backend")
        if environ.get("ZULIP_RUST_SEARCH_HIGHLIGHTS") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def available() -> bool:
    return callable(getattr(_backend, "assemble_search_highlights", None))


def assemble(rows: list[SearchHighlightRow]) -> dict[int, dict[str, str]] | None:
    native = getattr(_backend, "assemble_search_highlights", None)
    if not callable(native):
        return None
    return cast("dict[int, dict[str, str]] | None", native(rows))
