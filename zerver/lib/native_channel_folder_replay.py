"""Native replay of channel-folder events already selected by Django."""

from typing import cast

from zerver.lib.native_backend import load_native_contract

_backend = load_native_contract("ZULIP_RUST_CHANNEL_FOLDER_REPLAY", "CHANNEL_FOLDER_REPLAY_API")


def apply(state: dict[str, object], event: dict[str, object]) -> bool:
    native = getattr(_backend, "apply_channel_folder_event", None)
    if not callable(native) or type(state) is not dict or type(event) is not dict:
        return False
    return cast(bool, native(state.get("channel_folders"), event))
