# ruff: noqa: INP001
"""Rehearse the metadata-only Django thumbnail status with Rust and fallback."""

from __future__ import annotations

import argparse
import json
import socket
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

import django
from django.test import Client
from read_proxy_fixture import validate_fixture, validate_local_root, verify_private_database

if TYPE_CHECKING:
    from zerver.models import UserProfile

Metadata = dict[str, object]
Result = tuple[int, bytes, tuple[str, ...]]


@dataclass(frozen=True)
class StatusCase:
    actor: UserProfile | None
    path: str
    expected_status: int
    ready: bool | None = None
    declines: bool = False


def response(case: StatusCase, browser: bool, method: str, host: str) -> Result:
    client = Client()
    headers = {}
    if browser:
        prefix = "/json"
        if case.actor is not None:
            client.force_login(case.actor, backend="zproject.backends.DevAuthBackend")
    else:
        prefix = "/api/v1"
        if case.actor is not None:
            token = b64encode(f"{case.actor.delivery_email}:{case.actor.api_key}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
    result = client.generic(
        method,
        prefix + "/thumbnail/status/" + case.path,
        HTTP_HOST=host,
        headers=headers,
        secure=True,
    )
    response_headers = tuple(
        result.headers.get(name, "")
        for name in ("Content-Length", "Content-Type", "Content-Language", "Vary", "Cache-Control")
    )
    return result.status_code, result.content, response_headers


def run_parity() -> None:
    from _zulip_backend import thumbnail_status_complete

    from zerver.lib import native_thumbnail_status, thumbnail
    from zerver.models import Attachment, ImageAttachment, UserProfile

    assert native_thumbnail_status.available()
    owner = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    member = UserProfile.objects.get(realm=owner.realm, delivery_email="prospero@zulip.com")
    guest = UserProfile.objects.get(realm=owner.realm, delivery_email="polonius@zulip.com")
    foreign = UserProfile.objects.filter(
        realm__string_id="zephyr", is_bot=False, is_active=True
    ).first()
    assert guest.is_guest and foreign is not None

    def stored(width: int, height: int, animated: bool = False, ext: str = "webp") -> Metadata:
        return {
            "extension": ext,
            "max_width": width,
            "max_height": height,
            "animated": animated,
            "content_type": f"image/{ext}",
            "width": 90,
            "height": 70,
            "byte_size": 250,
        }

    still = stored(840, 560)
    anim = stored(840, 560, True)
    landscape = stored(4032, 3024)
    portrait = stored(3024, 4032)
    extra = stored(128, 128)
    legacy = {**still, "byte_size": None}

    def add(
        name: str,
        content_type: str | None,
        width: int,
        height: int,
        frames: int,
        metadata: list[Metadata] | None,
        *,
        public: bool = False,
    ) -> str:
        assert name.isascii() and name.isalpha()
        path = f"{owner.realm_id}/synthetic/{name}.png"
        Attachment.objects.create(
            file_name=f"{name}.png",
            path_id=path,
            owner=owner,
            realm=owner.realm,
            size=250,
            content_type=content_type,
            is_realm_public=public,
            is_web_public=False,
        )
        if metadata is not None:
            ImageAttachment.objects.create(
                realm=owner.realm,
                path_id=path,
                content_type=content_type,
                original_width_px=width,
                original_height_px=height,
                frames=frames,
                thumbnail_metadata=metadata,
            )
        return path

    cases = []
    for name, content_type, width, height, frames, metadata, ready in (
        ("blank", "image/png", 180, 100, 1, [], False),
        ("still", "image/png", 180, 100, 1, [still], True),
        ("onlyanimation", "image/gif", 180, 100, 1, [anim], False),
        ("animatedmissing", "image/gif", 180, 100, 2, [still], False),
        ("animated", "image/gif", 180, 100, 2, [still, anim], True),
        ("oldframes", "image/gif", 180, 100, 0, [still], False),
        ("landscape", "image/tiff", 180, 100, 1, [still, landscape], True),
        ("square", "image/tiff", 100, 100, 1, [still, landscape], True),
        ("wrongorientation", "image/tiff", 100, 180, 1, [still, landscape], False),
        ("portrait", "image/tiff; x=test", 100, 180, 1, [still, portrait], True),
        ("transcodedmissing", "image/heic", 180, 100, 1, [still], False),
        ("animatedtranscode", "image/heic", 180, 100, 2, [still, anim, landscape], True),
        ("legacyblank", "", 180, 100, 1, [still], True),
        ("legacyunknown", None, 180, 100, 1, [still], True),
        ("duplicate", "image/png", 180, 100, 1, [extra, still, still], True),
    ):
        cases.append(
            StatusCase(owner, add(name, content_type, width, height, frames, metadata), 200, ready)
        )

    path = add("publicphoto", "image/png", 90, 70, 1, [still], public=True)
    cases.append(StatusCase(member, path, 200, True))
    cases.append(StatusCase(guest, path, 400))
    cases.append(StatusCase(foreign, path, 400))
    private = cases[1].path
    cases.append(StatusCase(member, private, 400))
    cases.append(StatusCase(guest, private, 400))
    cases.append(StatusCase(None, private, 401))
    cases.append(StatusCase(owner, f"{owner.realm_id}/synthetic/nomatch.png", 400))
    cases.append(StatusCase(owner, add("notimage", "text/plain", 90, 70, 1, None), 400))
    cases.append(
        StatusCase(owner, add("legacysize", "image/png", 90, 70, 1, [legacy]), 200, True, True)
    )

    calls: list[bool | None] = []

    def actual(*args: object) -> bool | None:
        answer = thumbnail_status_complete(*args)
        calls.append(answer)
        return answer

    def decline(*args: object) -> None:
        calls.append(None)

    def with_backend(case: StatusCase, browser: bool, method: str, backend: object) -> Result:
        host = case.actor.realm.host if case.actor is not None else owner.realm.host
        with patch.object(native_thumbnail_status, "_backend", backend):
            return response(case, browser, method, host)

    new = SimpleNamespace(thumbnail_status_complete=actual)
    old = SimpleNamespace()
    unsupported = SimpleNamespace(thumbnail_status_complete=decline)

    def replay(case: StatusCase, browser: bool, method: str) -> None:
        calls.clear()
        reference = with_backend(case, browser, method, None)
        assert reference[0] == case.expected_status, (case.expected_status, reference[0])
        assert not calls
        if case.expected_status == 200 and method == "GET":
            assert json.loads(reference[1]) == {
                "has_thumbnail": case.ready,
                "result": "success",
                "msg": "",
            }
        if method == "HEAD":
            assert reference[1] == b""
        assert with_backend(case, browser, method, new) == reference
        native_calls = (
            [None if case.declines else case.ready] if case.expected_status == 200 else []
        )
        assert calls == native_calls
        calls.clear()
        assert with_backend(case, browser, method, unsupported) == reference
        assert calls == ([None] if case.expected_status == 200 else [])
        calls.clear()
        assert with_backend(case, browser, method, old) == reference
        assert not calls

    count = 0
    for case in cases:
        for browser in (False, True):
            for method in ("GET", "HEAD"):
                replay(case, browser, method)
                count += 1

    # Use the actual allowed formats configured by Django, not copies in Rust.
    format_test = StatusCase(
        owner, add("custom", "image/png", 90, 70, 1, [stored(100, 75, False, "jpeg")]), 200, True
    )
    custom = thumbnail.ThumbnailFormat("jpeg", 100, 75, animated=False, opts="Q=90")
    with patch.object(thumbnail, "THUMBNAIL_OUTPUT_FORMATS", (custom,)):
        for browser in (False, True):
            for method in ("GET", "HEAD"):
                replay(format_test, browser, method)
                count += 1
    assert count == 100
    print("100 Django request cases each: Python, compiled Rust, missing export, declined.")
    print(
        "No image decoder or storage needed. Authorization/missing cases never called the bridge."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("only the private loopback namespace is permitted")
    validate_fixture(root)
    django.setup()
    verify_private_database()
    run_parity()


if __name__ == "__main__":
    main()
