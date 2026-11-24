"""Optional readiness calculation for metadata Django already authorized."""

from __future__ import annotations

import os
from importlib import import_module
from types import ModuleType
from typing import TypeAlias, cast

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


_backend = load_backend("ZULIP_RUST_THUMBNAIL_STATUS")
_selection_backend = load_backend("ZULIP_RUST_TRANSCODE_SELECTION")
_plan_backend = load_backend("ZULIP_RUST_THUMBNAIL_PLAN")


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
