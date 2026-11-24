"""Optional formatting of plaintext already prepared by Django for mobile push."""

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
