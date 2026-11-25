"""Optional formatting of plaintext already prepared by Django for mobile push."""

from collections.abc import Callable
from importlib import import_module
from os import environ
from typing import cast

try:
    _backend = (
        import_module("_zulip_backend") if environ.get("ZULIP_RUST_PUSH_TEXT") == "1" else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


try:
    _ordered_list_backend = (
        import_module("_zulip_backend")
        if environ.get("ZULIP_RUST_MOBILE_ORDERED_LIST") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _ordered_list_backend = None


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
