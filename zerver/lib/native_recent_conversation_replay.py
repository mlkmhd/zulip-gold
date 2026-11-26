"""Optional replay of direct-message conversations already selected by Django."""

import os
from importlib import import_module
from typing import cast

from zerver.lib.native_backend import load_native_contract

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_RECENT_CONVERSATION_REPLAY") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


_pin_backend = load_native_contract(
    "ZULIP_RUST_RECENT_CONVERSATION_PIN_REPLAY", "RECENT_CONVERSATION_PIN_REPLAY_API"
)


def message(conversations: object, user_id: int, event_message: dict[str, object]) -> bool:
    native = getattr(_backend, "replay_recent_conversation_message", None)
    if not callable(native) or type(event_message) is not dict:
        return False
    # The body and all other message fields stay in Python.
    return cast(
        bool,
        native(
            conversations, user_id, event_message.get("id"), event_message.get("display_recipient")
        ),
    )


def pin(conversations: object, event: dict[str, object]) -> bool:
    backend = _backend if _backend is not None else _pin_backend
    native = getattr(backend, "replay_recent_conversation_pin", None)
    if not callable(native) or type(event) is not dict:
        return False
    return cast(bool, native(conversations, event.get("user_ids"), event.get("pinned")))
