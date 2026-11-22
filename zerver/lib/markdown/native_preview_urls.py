"""Opt-in native planning of external Markdown preview URLs."""

import os
import sys
from importlib import import_module
from typing import Literal, TypeAlias

PreviewValue: TypeAlias = Literal["youtube", "vimeo", "github_image", "corrected_image"]
DropboxPreview: TypeAlias = tuple[bool, str, str | None]

try:
    _backend = (
        import_module("_zulip_backend")
        if sys.version_info[:2] == (3, 12)
        and os.environ.get("ZULIP_RUST_MARKDOWN_PREVIEW_URLS") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def value(url: str, operation: PreviewValue) -> tuple[str | None] | None:
    native = getattr(_backend, "markdown_preview_url_value", None)
    return None if native is None else native(url, operation)


def dropbox(url: str) -> tuple[DropboxPreview | None] | None:
    native = getattr(_backend, "markdown_dropbox_preview", None)
    return None if native is None else native(url)
