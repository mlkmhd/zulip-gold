"""Native replay of events already selected by Django for one owner's state."""

from typing import cast

from zerver.lib.native_backend import load_native_contract

_backend = load_native_contract("ZULIP_RUST_SCHEDULE_REPLAY", "SCHEDULED_LIST_REPLAY_API")


def apply(state: dict[str, object], event: dict[str, object]) -> bool:
    native = getattr(_backend, "apply_scheduled_list_event", None)
    if not callable(native) or type(state) is not dict or type(event) is not dict:
        return False
    kind = event.get("type")
    if type(kind) is not str or kind not in ("scheduled_messages", "reminders"):
        return False
    # Do not pass the rest of the owner's register state to the extension.
    return cast(bool, native(state.get(kind), event))
