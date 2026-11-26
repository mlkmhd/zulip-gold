"""Opt-in presence formatting for rows already selected by Django."""

import os
from datetime import datetime, timezone
from importlib import import_module
from typing import TypeAlias, cast

from zerver.lib.native_backend import load_native_contract

_modern_backend = load_native_contract(
    "ZULIP_RUST_MODERN_PRESENCE_TIMESTAMPS", "PRESENCE_MODERN_TIMESTAMPS_API"
)
_legacy_display_backend = load_native_contract(
    "ZULIP_RUST_LEGACY_PRESENCE_DISPLAY", "PRESENCE_LEGACY_DISPLAY_API"
)
_single_response_backend = load_native_contract(
    "ZULIP_RUST_SINGLE_PRESENCE_RESPONSE", "PRESENCE_SINGLE_USER_RESPONSE_API"
)

LegacySpec: TypeAlias = tuple[int, str, str]
_DATE_POLICY = (datetime, timezone.utc)

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_PRESENCE_FORMAT") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def is_enabled() -> bool:
    return _backend is not None


def format_modern_timestamps(active: datetime, connected: datetime) -> dict[str, object] | None:
    native = getattr(_modern_backend, "format_modern_presence_timestamps", None)
    if native is not None:
        return cast(dict[str, object] | None, native(active, connected, _DATE_POLICY))
    return format_information(active, connected)


def legacy_display_enabled() -> bool:
    return _legacy_display_backend is not None or _backend is not None


def format_legacy_display(
    active: datetime, connected: datetime, spec: LegacySpec, *, aggregate: bool = False
) -> dict[str, object] | None:
    native = getattr(_legacy_display_backend, "format_presence_information", None)
    if native is not None:
        return cast(
            dict[str, object] | None, native(active, connected, _DATE_POLICY, spec, aggregate)
        )
    return format_information(active, connected, spec, aggregate=aggregate)


def format_rows(rows: object, spec: LegacySpec | None) -> dict[str, dict[str, object]] | None:
    native = getattr(_backend, "format_presence_rows", None)
    if native is None:
        return None
    return cast(dict[str, dict[str, object]] | None, native(rows, _DATE_POLICY, spec))


def format_information(
    active: datetime,
    connected: datetime,
    spec: LegacySpec | None = None,
    *,
    aggregate: bool = False,
) -> dict[str, object] | None:
    native = getattr(_backend, "format_presence_information", None)
    if native is None:
        return None
    return cast(
        dict[str, object] | None,
        native(active, connected, _DATE_POLICY, spec, aggregate),
    )


def single_response_enabled() -> bool:
    return _single_response_backend is not None or _backend is not None


def finish_single_user(
    modern: object, legacy: object, duration: int, offline_threshold: int
) -> dict[str, object] | None:
    backend = _single_response_backend or _backend
    native = getattr(backend, "finish_single_user_presence", None)
    if native is None:
        return None
    return cast(dict[str, object] | None, native(modern, legacy, duration, offline_threshold))
