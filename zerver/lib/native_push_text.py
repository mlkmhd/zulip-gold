"""Formatting of plaintext already prepared by Django for mobile push."""

from collections.abc import Callable
from typing import cast

from zerver.lib.native_backend import load_native_contract

_backend = load_native_contract("ZULIP_RUST_PUSH_TEXT", "MOBILE_PUSH_TEXT_API")
_ordered_list_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_ORDERED_LIST", "MOBILE_ORDERED_LIST_API"
)


OrderedListFormatter = Callable[[list[str], int, int], str | None]


def get_ordered_list_formatter() -> OrderedListFormatter | None:
    native = getattr(_ordered_list_backend, "format_mobile_ordered_list", None)
    return cast("OrderedListFormatter", native) if callable(native) else None


def quote(text: str) -> str | None:
    if _backend is None:
        return None
    native = getattr(_backend, "quote_mobile_push_text", None)
    return cast("str | None", native(text)) if callable(native) else None


def truncate(text: str) -> tuple[str, bool] | None:
    if _backend is None:
        return None
    native = getattr(_backend, "truncate_mobile_push_text", None)
    return cast("tuple[str, bool] | None", native(text)) if callable(native) else None
