"""Optional replay of channel-folder events already selected by Django."""

import os
from importlib import import_module
from typing import cast

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_CHANNEL_FOLDER_REPLAY") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def apply(state: dict[str, object], event: dict[str, object]) -> bool:
    native = getattr(_backend, "apply_channel_folder_event", None)
    if not callable(native) or type(state) is not dict or type(event) is not dict:
        return False
    return cast(bool, native(state.get("channel_folders"), event))
