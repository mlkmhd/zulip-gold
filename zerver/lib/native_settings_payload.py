"""Native shaping of settings and catalogs provided by Django."""

import os
from collections.abc import Mapping
from importlib import import_module
from typing import cast

from zerver.lib.native_backend import load_native_contract

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_SETTINGS_PAYLOAD") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


_catalog_backend = load_native_contract("ZULIP_RUST_SETTINGS_CATALOGS", "SETTINGS_CATALOGS_API")


def snapshot_properties(source: object, schema: Mapping[str, object]) -> dict[str, object]:
    native = getattr(_backend, "snapshot_settings_properties", None)
    if native is not None:
        result = cast("dict[str, object] | None", native(source, schema))
        if result is not None:
            return result
    return {name: getattr(source, name) for name in schema}


def format_emoji_choices(choices: object) -> list[dict[str, str]] | None:
    backend = _catalog_backend if _catalog_backend is not None else _backend
    native = getattr(backend, "format_settings_emoji_choices", None)
    if native is None:
        return None
    return cast("list[dict[str, str]] | None", native(choices))


def format_notification_sounds(filenames: list[str]) -> list[str] | None:
    backend = _catalog_backend if _catalog_backend is not None else _backend
    native = getattr(backend, "format_settings_notification_sounds", None)
    if native is None:
        return None
    return cast("list[str] | None", native(filenames))
