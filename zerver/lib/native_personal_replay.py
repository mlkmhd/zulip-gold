"""Optional replay of personal-list events already selected by Django."""

import os
from importlib import import_module
from typing import cast

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_PERSONAL_REPLAY") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def apply(state: dict[str, object], event: dict[str, object]) -> bool:
    native = getattr(_backend, "apply_personal_list_event", None)
    if not callable(native) or type(state) is not dict or type(event) is not dict:
        return False
    kind = event.get("type")
    if type(kind) is not str:
        return False
    if kind == "navigation_view":
        key = "navigation_views"
    elif kind in ("saved_snippets", "drafts"):
        key = kind
    else:
        return False
    # Only the list already selected for the current account crosses the bridge.
    return cast(bool, native(state.get(key), event))
