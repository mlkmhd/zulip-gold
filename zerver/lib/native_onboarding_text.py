"""Optional formatting for already prepared onboarding and announcement text."""

from collections.abc import Callable
from importlib import import_module
from os import environ
from typing import cast

try:
    _backend = (
        import_module("_zulip_backend")
        if environ.get("ZULIP_RUST_ONBOARDING_TEXT") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def _optional_symbol(name: str) -> object:
    symbol = getattr(_backend, name, None)
    return symbol if callable(symbol) else None


flatten_prepared_onboarding_newlines = cast(
    "Callable[[str], str | None] | None",
    _optional_symbol("flatten_prepared_onboarding_newlines"),
)
wrap_prepared_welcome_quote = cast(
    "Callable[[str, str, str], str | None] | None",
    _optional_symbol("wrap_prepared_welcome_quote"),
)
