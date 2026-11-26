"""Native operations on events that Tornado has already selected."""

import os
from collections import deque
from importlib import import_module
from typing import cast

from zerver.lib.native_backend import load_native_contract

_legacy_requested = os.environ.get("ZULIP_RUST_EVENT_QUEUE") == "1"
_classification_backend = (
    None
    if _legacy_requested or os.environ.get("ZULIP_RUST_PREPARED_EVENT_CLASSIFICATION") == "0"
    else load_native_contract(
        "ZULIP_RUST_PREPARED_EVENT_CLASSIFICATION", "PREPARED_EVENT_CLASSIFICATION_API"
    )
)

_fold_backend = (
    None
    if _legacy_requested or os.environ.get("ZULIP_RUST_PREPARED_EVENT_FOLD") == "0"
    else load_native_contract("ZULIP_RUST_PREPARED_EVENT_FOLD", "PREPARED_EVENT_FOLD_API")
)

try:
    _backend = import_module("_zulip_backend") if _legacy_requested else None
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def classify(event: object) -> str | None:
    backend = _backend if _legacy_requested else _classification_backend
    native = getattr(backend, "classify_queued_event", None)
    if native is None:
        return None
    return cast("str | None", native(event))


def fold_flag_event(previous: object, current: object) -> bool:
    backend = _backend if _legacy_requested else _fold_backend
    native = getattr(backend, "fold_queued_flag_event", None)
    return native is not None and cast(bool, native(previous, current))


def merge(queue: object, virtual_events: object) -> list[dict[str, object]] | None:
    native = getattr(_backend, "merge_queued_events", None)
    if native is None:
        return None
    return cast("list[dict[str, object]] | None", native(queue, deque, virtual_events))
