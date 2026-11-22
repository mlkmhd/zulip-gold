"""Optional replay of events already selected by Django for one owner's state."""

import os
from importlib import import_module
from typing import cast

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_SCHEDULE_REPLAY") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def apply(state: dict[str, object], event: dict[str, object]) -> bool:
    native = getattr(_backend, "apply_scheduled_list_event", None)
    if not callable(native) or type(state) is not dict or type(event) is not dict:
        return False
    kind = event.get("type")
    if type(kind) is not str or kind not in ("scheduled_messages", "reminders"):
        return False
    # Do not pass the rest of the owner's register state to the extension.
    return cast(bool, native(state.get(kind), event))
