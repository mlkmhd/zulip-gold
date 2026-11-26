"""Native replay of personal-list events already selected by Django."""

import os
from importlib import import_module
from typing import cast

from zerver.lib.native_backend import load_native_contract

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


_draft_backend = load_native_contract("ZULIP_RUST_DRAFT_REPLAY", "DRAFT_REPLAY_API")


def apply(state: dict[str, object], event: dict[str, object]) -> bool:
    if type(state) is not dict or type(event) is not dict:
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
    backend = _backend
    if backend is None and kind == "drafts":
        backend = _draft_backend
    native = getattr(backend, "apply_personal_list_event", None)
    if not callable(native):
        return False
    # Only the list already selected for the current account crosses the bridge.
    return cast(bool, native(state.get(key), event))
