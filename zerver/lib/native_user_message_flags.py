"""Optional public labels for receipt masks already selected by Django."""

import os
from collections.abc import Iterable
from importlib import import_module
from typing import cast

from zerver.lib.native_backend import load_native_contract
from zerver.models import UserMessage
from zerver.models.messages import AbstractUserMessage

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_USER_MESSAGE_FLAGS") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


_prepared_fetch_backend = load_native_contract(
    "ZULIP_RUST_PREPARED_MESSAGE_FETCH_LABELS", "PREPARED_MESSAGE_FETCH_LABELS_API"
)
_reference_flags_list = UserMessage.flags_list
_reference_flags_list_for_flags = UserMessage.flags_list_for_flags


def available(*, prepared_fetch: bool = False) -> bool:
    if _backend is not None or not prepared_fetch:
        return callable(getattr(_backend, "label_user_message_flags", None))
    return (
        callable(getattr(_prepared_fetch_backend, "label_user_message_flags", None))
        and UserMessage.flags_list is _reference_flags_list
        and UserMessage.flags_list_for_flags is _reference_flags_list_for_flags
        and type(UserMessage.ALL_FLAGS) is list
        and type(UserMessage.NON_API_FLAGS) is set
        and UserMessage.NON_API_FLAGS is AbstractUserMessage.NON_API_FLAGS
        and all(type(name) is str for name in UserMessage.ALL_FLAGS)
        and all(type(name) is str for name in UserMessage.NON_API_FLAGS)
    )


def labels_by_id(
    rows: Iterable[tuple[int, int]], *, prepared_fetch: bool = False
) -> dict[int, list[str]] | None:
    if not available(prepared_fetch=prepared_fetch):
        return None
    backend = _prepared_fetch_backend if prepared_fetch and _backend is None else _backend
    native = getattr(backend, "label_user_message_flags", None)
    if not callable(native):
        return None
    labels = tuple(
        None if name in UserMessage.NON_API_FLAGS else name for name in UserMessage.ALL_FLAGS
    )
    return cast("dict[int, list[str]] | None", native(list(rows), labels))
