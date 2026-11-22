"""Optional public labels for receipt masks already selected by Django."""

import os
from collections.abc import Iterable
from importlib import import_module
from typing import cast

from zerver.models import UserMessage

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


def available() -> bool:
    return callable(getattr(_backend, "label_user_message_flags", None))


def labels_by_id(rows: Iterable[tuple[int, int]]) -> dict[int, list[str]] | None:
    native = getattr(_backend, "label_user_message_flags", None)
    if not callable(native):
        return None
    labels = tuple(
        None if name in UserMessage.NON_API_FLAGS else name for name in UserMessage.ALL_FLAGS
    )
    return cast("dict[int, list[str]] | None", native(list(rows), labels))
