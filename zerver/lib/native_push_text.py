"""Formatting of plaintext already prepared by Django for mobile push."""

from collections.abc import Callable
from typing import cast

from zerver.lib.native_backend import load_native_contract

_backend = load_native_contract("ZULIP_RUST_PUSH_TEXT", "MOBILE_PUSH_TEXT_API")
_ordered_list_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_ORDERED_LIST", "MOBILE_ORDERED_LIST_API"
)


_quote_collapse_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_QUOTE_COLLAPSE", "MOBILE_QUOTE_COLLAPSE_API"
)


def plan_quote_collapse(statuses: list[bool]) -> list[bool | None] | None:
    native = getattr(_quote_collapse_backend, "plan_mobile_quote_collapse", None)
    return cast("list[bool | None] | None", native(statuses)) if callable(native) else None


_spoiler_header_backend = load_native_contract(
    "ZULIP_RUST_MOBILE_SPOILER_HEADER", "MOBILE_SPOILER_HEADER_API"
)


def format_spoiler_header(text: str) -> str | None:
    native = getattr(_spoiler_header_backend, "format_mobile_spoiler_header", None)
    return cast("str | None", native(text)) if callable(native) else None


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
