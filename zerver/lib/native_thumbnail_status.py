"""Optional readiness calculation for metadata Django already authorized."""

from __future__ import annotations

import os
from importlib import import_module
from typing import TypeAlias, cast

ThumbnailKey: TypeAlias = tuple[str, int, int, bool]

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_THUMBNAIL_STATUS") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


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
