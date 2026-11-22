"""Opt-in operations on events that Tornado has already selected."""

import os
from collections import deque
from importlib import import_module
from typing import cast

try:
    _backend = (
        import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_EVENT_QUEUE") == "1" else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def classify(event: object) -> str | None:
    native = getattr(_backend, "classify_queued_event", None)
    if native is None:
        return None
    return cast("str | None", native(event))


def fold_flag_event(previous: object, current: object) -> bool:
    native = getattr(_backend, "fold_queued_flag_event", None)
    return native is not None and cast(bool, native(previous, current))


def merge(queue: object, virtual_events: object) -> list[dict[str, object]] | None:
    native = getattr(_backend, "merge_queued_events", None)
    if native is None:
        return None
    return cast("list[dict[str, object]] | None", native(queue, deque, virtual_events))
