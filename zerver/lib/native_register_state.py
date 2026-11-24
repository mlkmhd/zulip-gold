"""Opt-in final shaping of register state after Django visibility filtering."""

import os
from importlib import import_module
from typing import cast

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_REGISTER_STATE") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None

try:
    _profile_prune_backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_REGISTER_PROFILE_PRUNE") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _profile_prune_backend = None


def prune_profile_fields(raw: object, fields: object) -> bool:
    if _profile_prune_backend is None:
        return False
    native = getattr(_profile_prune_backend, "prune_register_custom_profile_fields", None)
    return callable(native) and cast(bool, native(raw, fields))


def partition_users(
    raw: object,
) -> tuple[list[dict[str, object]], list[dict[str, object]]] | None:
    native = getattr(_backend, "partition_register_users", None)
    if native is None:
        return None
    return cast("tuple[list[dict[str, object]], list[dict[str, object]]] | None", native(raw))


def fill_legacy_notifications(subscribed: object, unsubscribed: object, profile: object) -> bool:
    native = getattr(_backend, "fill_legacy_register_notifications", None)
    return native is not None and cast(bool, native(subscribed, unsubscribed, profile))


def fill_legacy_topics(topics: object, fallback: str) -> bool:
    native = getattr(_backend, "fill_legacy_register_topics", None)
    return native is not None and cast(bool, native(topics, fallback))
