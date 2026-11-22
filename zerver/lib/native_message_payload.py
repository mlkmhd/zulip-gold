"""Opt-in Rust assembly of selected message payload fields."""

import os
import sys
from collections.abc import Callable, Iterable, Mapping
from importlib import import_module
from typing import TypeVar, cast

MessageT = TypeVar("MessageT", bound=Mapping[str, object])

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_MESSAGE_PAYLOAD") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def is_enabled() -> bool:
    return _backend is not None


def finalize_content(
    obj: Mapping[str, object],
    *,
    apply_markdown: bool,
    allow_empty_topic: bool,
    empty_topic_fallback: str,
    plain_reply_fields: Callable[..., dict[str, str]] | None,
) -> bool:
    native = getattr(_backend, "finalize_message_payload_content", None)
    if (
        native is None
        or type(apply_markdown) is not bool
        or type(allow_empty_topic) is not bool
        or type(empty_topic_fallback) is not str
    ):
        return False
    return cast(
        bool,
        native(obj, apply_markdown, allow_empty_topic, empty_topic_fallback, plain_reply_fields),
    )


def _is_values_queryset(rows: object) -> bool:
    # A live QuerySet already has its Django module loaded. Do not inspect
    # custom iterable classes or import Django for the service-free path.
    query_module = sys.modules.get("django.db.models.query")
    return (
        query_module is not None
        and type(rows) is getattr(query_module, "QuerySet", None)
        and getattr(rows, "_iterable_class", None) is getattr(query_module, "ValuesIterable", None)
    )


def _message_rows(rows: object) -> object:
    if _is_values_queryset(rows):
        # Materialize only an ordinary Django Values QuerySet. Django's
        # result cache keeps the same row objects for later reference use.
        return list(cast(Iterable[object], rows))
    return rows


def assemble_reactions(messages: list[MessageT], reactions: object) -> list[MessageT] | None:
    native = getattr(_backend, "assemble_message_reactions", None)
    if native is None:
        return None
    return cast(
        list[MessageT] | None,
        native(_message_rows(messages), reactions, _is_values_queryset(reactions)),
    )


def assemble_submessages(messages: object, submessages: object) -> bool:
    native = getattr(_backend, "assemble_message_submessages", None)
    if native is None:
        return False
    return cast(bool, native(_message_rows(messages), submessages))


def assemble_cached_frame(
    fields: tuple[object, ...],
    edit: tuple[int, object] | None,
    rendered_content: object,
    reactions: object,
    submessages: object,
) -> dict[str, object] | None:
    native = getattr(_backend, "assemble_cached_message_frame", None)
    if native is None:
        return None
    return cast(
        dict[str, object] | None,
        native(fields, edit, rendered_content, reactions, submessages),
    )


def adapt_mobile_events(events: object, plain_reply_fields: Callable[..., dict[str, str]]) -> bool:
    native = getattr(_backend, "adapt_mobile_message_events", None)
    if native is None:
        return False
    return cast(bool, native(events, plain_reply_fields))
