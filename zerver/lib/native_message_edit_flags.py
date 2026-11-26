"""Native calculation of rendering-derived receipt flag changes."""

import os
from collections.abc import Set as AbstractSet
from importlib import import_module
from typing import cast

from django.db.models import QuerySet
from django.db.models.query import ModelIterable

from zerver.lib.native_backend import load_native_contract
from zerver.models import UserMessage

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_MESSAGE_EDIT_FLAGS") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


_prepared_backend = load_native_contract(
    "ZULIP_RUST_PREPARED_MESSAGE_EDIT_FLAGS", "PREPARED_MESSAGE_EDIT_FLAGS_API"
)


def available(rows: object) -> bool:
    if _backend is not None:
        return callable(getattr(_backend, "plan_user_message_flag_deltas", None))
    if not callable(getattr(_prepared_backend, "plan_user_message_flag_deltas", None)):
        return False
    if type(rows) is list:
        return True
    # A plain model QuerySet is fully fetched by __iter__ even in the
    # reference path. An arbitrary iterator need not have that behavior.
    return (
        type(rows) is QuerySet
        and rows.model is UserMessage
        and rows._iterable_class is ModelIterable
        and rows.query.deferred_loading == (frozenset(), True)
    )


def plan(
    rows: list[UserMessage],
    alerts: AbstractSet[int],
    mentions: AbstractSet[int],
    topics: AbstractSet[int],
    stream_mention: bool,
) -> list[tuple[int, int]] | None:
    backend = _backend if _backend is not None else _prepared_backend
    native = getattr(backend, "plan_user_message_flag_deltas", None)
    if not callable(native) or any(type(row) is not UserMessage for row in rows):
        return None
    if _backend is None and (
        type(stream_mention) is not bool
        or any(type(ids) not in (set, frozenset) for ids in (alerts, mentions, topics))
        or any(
            row._state.adding or row.get_deferred_fields() or type(row.pk) is not int
            for row in rows
        )
        or len({row.pk for row in rows}) != len(rows)
    ):
        return None
    flags = UserMessage.flags
    masks = (
        flags.has_alert_word.mask,
        flags.mentioned.mask,
        flags.stream_wildcard_mentioned.mask,
        flags.topic_wildcard_mentioned.mask,
    )
    return cast(
        "list[tuple[int, int]] | None",
        native(
            [(row.user_profile_id, int(row.flags)) for row in rows],
            masks,
            (alerts, mentions, topics),
            stream_mention,
        ),
    )
