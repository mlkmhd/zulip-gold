"""Replay the status row that Django has already selected."""

from os import environ

from zerver.lib.native_backend import load_native_contract

_FLAG = "ZULIP_RUST_USER_STATUS_REPLAY"
_explicit_replay = environ.get(_FLAG) == "1"
_backend = load_native_contract(_FLAG, "USER_STATUS_REPLAY_API")

_MISSING = object()


def apply(statuses: object, user_id: str, event: object) -> bool:
    if _backend is None:
        return False
    native = getattr(_backend, "apply_user_status_row_event", None)
    if (
        not callable(native)
        or type(statuses) is not dict
        or type(user_id) is not str
        or type(event) is not dict
    ):
        return False
    # Custom dictionary keys can observe reference-path lookup and insertion.
    if not _explicit_replay and any(type(key) is not str for key in statuses):
        return False
    row = statuses.get(user_id, _MISSING)
    is_new = row is _MISSING
    if is_new:
        row = {}
    elif row is statuses or row is event:
        return False

    # A declined edit leaves the original row untouched. Only Django owns
    # inserting or removing the row in its already-selected outer map.
    keep = native(row, event)
    if keep is None:
        return False
    if keep:
        if is_new:
            statuses[user_id] = row
    elif not is_new:
        del statuses[user_id]
    return True
