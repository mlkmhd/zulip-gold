"""Optional readiness calculation for metadata Django already authorized."""

from __future__ import annotations

import os
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, TypeAlias, cast

from zerver.lib.native_backend import load_native_contract

if TYPE_CHECKING:
    from zerver.lib.thumbnail import BaseThumbnailFormat

ThumbnailKey: TypeAlias = tuple[str, int, int, bool]


def load_backend(flag: str) -> ModuleType | None:
    if os.environ.get(flag) != "1":
        return None
    try:
        return import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        return None


_backend = load_native_contract("ZULIP_RUST_THUMBNAIL_STATUS", "THUMBNAIL_READINESS_API")
_selection_backend = load_native_contract(
    "ZULIP_RUST_TRANSCODE_SELECTION", "PREPARED_TRANSCODE_SELECTION_API"
)
_plan_backend = load_native_contract("ZULIP_RUST_THUMBNAIL_PLAN", "THUMBNAIL_MISSING_PLAN_API")
_stored_variant_backend = load_native_contract(
    "ZULIP_RUST_THUMBNAIL_STORED_VARIANT", "THUMBNAIL_STORED_VARIANT_API"
)


def available() -> bool:
    return callable(getattr(_backend, "thumbnail_status_complete", None))


def completeness(
    metadata: object,
    formats: tuple[ThumbnailKey, ...],
    transcode: ThumbnailKey | None,
    frames: int,
    original_width: int,
    original_height: int,
) -> bool | None:
    native = getattr(_backend, "thumbnail_status_complete", None)
    if native is None:
        return None
    return cast(
        "bool | None", native(metadata, formats, transcode, frames, original_width, original_height)
    )


def selection_available() -> bool:
    return callable(getattr(_selection_backend, "thumbnail_transcode_indexes", None))


def transcode_indexes(candidates: list[object]) -> list[int] | None:
    native = getattr(_selection_backend, "thumbnail_transcode_indexes", None)
    if not callable(native):
        return None
    return cast("list[int] | None", native(candidates))


def plan_available() -> bool:
    return callable(getattr(_plan_backend, "thumbnail_missing_indexes", None))


def missing_indexes(
    metadata: object, formats: tuple[ThumbnailKey, ...], frames: int
) -> list[int] | None:
    native = getattr(_plan_backend, "thumbnail_missing_indexes", None)
    if not callable(native):
        return None
    return cast("list[int] | None", native(metadata, formats, frames))


def stored_variant_available() -> bool:
    return callable(getattr(_stored_variant_backend, "thumbnail_has_stored_variant", None))


def has_stored_variant(metadata: object, requested: BaseThumbnailFormat) -> bool | None:
    native = getattr(_stored_variant_backend, "thumbnail_has_stored_variant", None)
    if not callable(native):
        return None
    return cast(
        "bool | None",
        native(
            metadata,
            (requested.extension, requested.max_width, requested.max_height, requested.animated),
        ),
    )
