"""Native formatting for already prepared onboarding and announcement text."""

from collections.abc import Callable
from typing import cast

from zerver.lib.native_backend import load_native_contract

_backend = load_native_contract("ZULIP_RUST_ONBOARDING_TEXT", "ONBOARDING_TEXT_API")


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
