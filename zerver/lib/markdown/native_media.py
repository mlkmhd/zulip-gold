import hashlib
import hmac
import mimetypes
import os
import posixpath
import re
import sys
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast
from urllib import parse as urlparse

from markdown import inlinepatterns

if TYPE_CHECKING:
    from zerver.lib.markdown import AudioInlineProcessor, ImageInlineProcessor, ZulipMarkdown

MimeInputs: TypeAlias = tuple[dict[str, str], set[str], dict[str, str], set[str], set[str]]
AudioInputs: TypeAlias = tuple[MimeInputs, str, str, str | None, str, str | None]
MediaInputs: TypeAlias = tuple[
    str | None, set[str] | None, dict[str, tuple[str, str, str | None]] | None, AudioInputs
]


class MimeDatabase(Protocol):
    suffix_map: dict[str, str]
    encodings_map: dict[str, str]
    types_map: tuple[dict[str, str], ...]

    def guess_type(self, url: str, strict: bool = True) -> tuple[str | None, str | None]: ...


_CAMO_CALLBACKS = (hmac.new, hmac.HMAC, hashlib.sha1)
_SURROGATE_RE = re.compile("[\ud800-\udfff]")
_MIME_CALLBACKS = (
    mimetypes.guess_type,
    mimetypes.MimeTypes.guess_type,
    posixpath.splitext,
    posixpath.splitdrive,
    os.fspath,
    urlparse.urlparse,
)


def standard_pattern(pattern: "AudioInlineProcessor | ImageInlineProcessor") -> bool:
    from zerver.lib.markdown import (
        AudioInlineProcessor,
        _native_media_audio_callbacks,
        _native_media_image_callbacks,
    )

    names = (
        ("maybe_audio_element", "zulip_specific_src_changes", "is_supported_audio_url")
        if type(pattern) is AudioInlineProcessor
        else ("zulip_specific_src_changes",)
    )
    callbacks = (
        _native_media_audio_callbacks
        if type(pattern) is AudioInlineProcessor
        else _native_media_image_callbacks
    )
    return all(
        getattr(getattr(pattern, name), "__func__", None) is callback
        for name, callback in zip(names, callbacks, strict=True)
    )


def snapshot(renderer: "ZulipMarkdown") -> MediaInputs | None:
    from django.conf import settings

    from zerver.lib import camo, markdown, subdomains
    from zerver.lib.markdown import (
        DbData,
        MessageRenderingResult,
        ZulipMarkdown,
        _native_media_callbacks,
        _native_media_realm_callbacks,
        _native_media_url_callbacks,
    )
    from zerver.lib.thumbnail import AttachmentData, MarkdownImageMetadata
    from zerver.models import Realm

    if (
        sys.version_info[:2] != (3, 12)
        or os.path is not posixpath
        or type(renderer) is not ZulipMarkdown
        or markdown.maybe_add_attachment_path_id is not _native_media_callbacks[0]
        or vars(markdown).get("guess_type") is not _MIME_CALLBACKS[0]
        or vars(markdown).get("is_static_or_current_realm_url") is not _native_media_callbacks[1]
        or vars(markdown).get("get_camo_url") is not _native_media_callbacks[2]
        or camo.get_camo_url is not _native_media_callbacks[2]
        or camo.generate_camo_url is not _native_media_callbacks[3]
        or vars(subdomains).get("urlsplit") is not _native_media_url_callbacks[0]
        or vars(markdown).get("urljoin") is not _native_media_url_callbacks[1]
        or inlinepatterns.ImageInlineProcessor.handleMatch is not _native_media_callbacks[4]
        or getattr(markdown._rust_markdown_upload_paths, "local_markdown_upload_path", None)
        is not _native_media_callbacks[5]
        or (
            mimetypes.guess_type,
            mimetypes.MimeTypes.guess_type,
            posixpath.splitext,
            posixpath.splitdrive,
            os.fspath,
            urlparse.urlparse,
        )
        != _MIME_CALLBACKS
        or (hmac.new, hmac.HMAC, hashlib.sha1) != _CAMO_CALLBACKS
        or vars(camo).get("hmac") is not hmac
        or vars(camo).get("hashlib") is not hashlib
        or settings.LOCAL_UPLOADS_DIR is None
    ):
        return None
    rendering = renderer.zulip_rendering_result
    if (
        type(rendering) is not MessageRenderingResult
        or type(rendering.potential_attachment_path_ids) is not list
        or type(cast(object, rendering.has_image)) is not bool
    ):
        return None
    size = 0

    def valid_text(value: object) -> bool:
        nonlocal size
        if type(value) is not str or _SURROGATE_RE.search(value):
            return False
        size += len(value)
        return size <= 1_000_000

    values = (settings.STATIC_URL, settings.EXTERNAL_URI_SCHEME, settings.CAMO_URI)
    if not all(valid_text(value) for value in values) or (
        settings.CAMO_KEY is not None and not valid_text(settings.CAMO_KEY)
    ):
        return None
    if settings.CAMO_URI and settings.CAMO_KEY is None:
        return None
    realm = getattr(renderer, "zulip_realm", None)
    host = None
    if realm is not None:
        if (
            type(realm) is not Realm
            or (Realm.host, Realm.subdomain, Realm.host_for_subdomain, vars(Realm)["string_id"])
            != _native_media_realm_callbacks
            or not valid_text(vars(realm).get("string_id"))
            or not valid_text(settings.EXTERNAL_HOST)
            or type(settings.REALM_HOSTS) is not dict
            or len(settings.REALM_HOSTS) > 100_000
            or any(
                not valid_text(key) or not valid_text(value)
                for key, value in settings.REALM_HOSTS.items()
            )
        ):
            return None
        host = realm.host
        if not valid_text(host):
            return None
    raw_db = vars(mimetypes).get("_db")
    if type(raw_db) is not mimetypes.MimeTypes:
        return None
    db = cast(MimeDatabase, raw_db)
    supported_audio = vars(markdown).get("AUDIO_INLINE_MIME_TYPES")
    if (
        getattr(db.guess_type, "__func__", None) is not _MIME_CALLBACKS[1]
        or type(db.types_map) is not tuple
        or len(db.types_map) != 2
        or type(supported_audio) not in (list, tuple)
        or type(urlparse.uses_params) is not list
    ):
        return None
    supported_audio = cast(list[str] | tuple[str, ...], supported_audio)
    tables = (db.suffix_map, db.encodings_map, db.types_map[1])
    if any(type(table) is not dict for table in tables) or (
        sum(len(table) for table in tables) + len(supported_audio) + len(urlparse.uses_params)
        > 100_000
    ):
        return None
    if any(
        not valid_text(key) or not valid_text(value)
        for table in tables
        for key, value in table.items()
    ) or any(not valid_text(value) for value in (*supported_audio, *urlparse.uses_params)):
        return None
    audio = None
    images = None
    data = renderer.zulip_db_data
    if data is not None:
        if type(data) is not DbData or type(data.user_upload_previews) is not AttachmentData:
            return None
        previews = data.user_upload_previews
        if (
            type(previews.audio_path_ids) is not set
            or type(previews.image_metadata) is not dict
            or len(previews.audio_path_ids) + len(previews.image_metadata) > 100_000
            or any(not valid_text(path) for path in previews.audio_path_ids)
        ):
            return None
        images = {}
        for path, metadata in previews.image_metadata.items():
            if not valid_text(path) or type(metadata) is not MarkdownImageMetadata:
                return None
            if any(
                type(value) is not int or value.bit_length() > 2000
                for value in (metadata.original_width_px, metadata.original_height_px)
            ) or (
                metadata.original_content_type is not None
                and not valid_text(metadata.original_content_type)
            ):
                return None
            width, height = str(metadata.original_width_px), str(metadata.original_height_px)
            if not valid_text(width) or not valid_text(height):
                return None
            images[path] = width, height, metadata.original_content_type
        audio = previews.audio_path_ids.copy()
    mime = (
        db.suffix_map.copy(),
        set(db.encodings_map),
        db.types_map[1].copy(),
        set(supported_audio),
        set(urlparse.uses_params),
    )
    return (
        host,
        audio,
        images,
        (mime, cast(str, values[0]), values[1], None, values[2], settings.CAMO_KEY),
    )
