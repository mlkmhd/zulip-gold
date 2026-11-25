"""Rust assembly of selected message payload fields."""

import os
import sys
from collections.abc import Callable, Iterable, Mapping
from importlib import import_module
from typing import TypeVar, cast

from zerver.lib.native_backend import load_native_contract

MessageT = TypeVar("MessageT", bound=Mapping[str, object])
_requested = os.environ.get("ZULIP_RUST_MESSAGE_PAYLOAD") == "1"

try:
    _backend = import_module("_zulip_backend") if _requested else None
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


_default_content_backend = (
    None
    if _requested
    else load_native_contract("ZULIP_RUST_MESSAGE_PAYLOAD", "MESSAGE_CONTENT_FINALIZER_API")
)


def is_enabled() -> bool:
    return _backend is not None


def _content_backend() -> object:
    return _backend if _backend is not None else _default_content_backend


def has_content_finalizer() -> bool:
    return callable(getattr(_content_backend(), "finalize_message_payload_content", None))


def _ordinary_dict(obj: object) -> bool:
    return type(obj) is dict and all(type(key) is str for key in obj)


def _ordinary_finalizer_options(
    apply_markdown: object, allow_empty_topic: object, empty_topic_fallback: object
) -> bool:
    return (
        type(apply_markdown) is bool
        and type(allow_empty_topic) is bool
        and type(empty_topic_fallback) is str
    )


def _plain_string(value: object) -> bool:
    return value is None or type(value) is str


def _apply_finalized_content(
    obj: dict[str, object],
    payload: dict[str, object],
    history: list[dict[str, object]] | None,
    projected_history: list[dict[str, object]] | None,
    edit_fields: tuple[str, ...],
    *,
    apply_markdown: bool,
    plain_reply_links: bool,
) -> None:
    # Match the reference assignment order, including when content_type is new.
    obj["content_type"] = payload["content_type"]
    if apply_markdown and "content" in payload:
        obj["content"] = payload["content"]
    if plain_reply_links and "match_content" in payload:
        obj["match_content"] = payload["match_content"]

    if history is None:
        return
    updates = cast(list[dict[str, object]], payload["edit_history"])
    if plain_reply_links:
        if updates is projected_history:
            # The callback raised before the native finalizer replaced the list.
            return
        history = [row.copy() for row in history]
        obj["edit_history"] = history
    for row, update in zip(history, updates, strict=True):
        for field in edit_fields:
            if field != "prev_rendered_content_version" and field in update:
                row[field] = update[field]
        if "prev_rendered_content_version" not in update:
            row.pop("prev_rendered_content_version", None)


def finalize_content(
    obj: dict[str, object],
    *,
    apply_markdown: bool,
    allow_empty_topic: bool,
    empty_topic_fallback: str,
    plain_reply_fields: Callable[..., dict[str, str]] | None,
) -> bool:
    native = getattr(_content_backend(), "finalize_message_payload_content", None)
    if native is None or not _ordinary_finalizer_options(
        apply_markdown, allow_empty_topic, empty_topic_fallback
    ):
        return False
    if not _ordinary_dict(obj) or not _plain_string(obj.get("content_type")):
        return False

    # Django owns the full card. Send only what this operation reads, not raw
    # message text, sender fields, or identity/raw text from the edit rows.
    payload: dict[str, object] = {}
    if apply_markdown:
        if "rendered_content" not in obj or not _plain_string(obj.get("content")):
            return False
        rendered = obj["rendered_content"]
        if not _plain_string(rendered):
            return False
        payload["rendered_content"] = rendered
    if plain_reply_fields is not None and "match_content" in obj:
        match = obj["match_content"]
        if not _plain_string(match):
            return False
        payload["match_content"] = match

    history: list[dict[str, object]] | None = None
    projected_history: list[dict[str, object]] | None = None
    edit_fields: tuple[str, ...] = ()
    if "edit_history" in obj:
        projected_history = []
        edit_fields = ("prev_rendered_content_version",)
        if plain_reply_fields is not None:
            edit_fields += ("prev_rendered_content",)
        if not allow_empty_topic:
            edit_fields += ("prev_topic", "topic")
        if type(obj["edit_history"]) is not list:
            return False
        history = cast(list[dict[str, object]], obj["edit_history"])
        for row in history:
            if not _ordinary_dict(row):
                return False
            projected_row: dict[str, object] = {}
            for field in edit_fields:
                if field not in row:
                    continue
                value = row[field]
                if field == "prev_rendered_content_version":
                    if value is not None and type(value) is not int:
                        return False
                    # Rust only removes this field; it needs no version value.
                    value = None
                elif field == "prev_rendered_content":
                    if not _plain_string(value):
                        return False
                elif type(value) is not str:
                    return False
                elif value:
                    # Only the legacy substitution of an empty topic is needed.
                    continue
                projected_row[field] = value
            projected_history.append(projected_row)
        payload["edit_history"] = projected_history

    # A decline has no effect on the Django card. If the callback raises,
    # retain the reference path's partial assignments without replaying it.
    accepted = True
    try:
        accepted = cast(
            bool,
            native(
                payload, apply_markdown, allow_empty_topic, empty_topic_fallback, plain_reply_fields
            ),
        )
    finally:
        if accepted and "content_type" in payload:
            _apply_finalized_content(
                obj,
                payload,
                history,
                projected_history,
                edit_fields,
                apply_markdown=apply_markdown,
                plain_reply_links=plain_reply_fields is not None,
            )
    return accepted


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
