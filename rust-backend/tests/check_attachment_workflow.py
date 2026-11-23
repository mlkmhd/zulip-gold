# ruff: noqa: INP001
"""Rehearse private upload, attachment lists, download, and rollback."""

from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
from base64 import b64encode
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast
from urllib.parse import urlencode, urlsplit

import django
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from read_proxy_fixture import (
    PrivateReadProxy,
    ReadResult,
    compare_read_headers,
    private_read_proxy,
    validate_fixture,
    validate_local_root,
    verify_private_database,
    wait_until,
)

from zerver.lib.test_rust_http import rust_http_service

if TYPE_CHECKING:
    from zerver.models import UserProfile

ROOT = Path(__file__).resolve().parents[2]
MAP = "attachments-map.conf"
ROUTES = "attachments-routes.conf"
API = "/api/v1/attachments"
WEB = "/json/attachments"


@dataclass(frozen=True)
class Actor:
    user_id: int
    host: str
    basic: dict[str, str] = field(repr=False)
    web: dict[str, str] = field(repr=False)


@dataclass(frozen=True)
class Uploaded:
    id: int
    name: str
    path_id: str
    url: str
    contents: bytes
    message_id: int


@contextmanager
def actor(user: UserProfile) -> Iterator[Actor]:
    session = SessionStore()
    session.update(
        {
            "_auth_user_id": str(user.id),
            "_auth_user_backend": "zproject.backends.DevAuthBackend",
            "_auth_user_hash": user.get_session_auth_hash(),
        }
    )
    session.create()
    auth = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
    try:
        yield Actor(
            user.id,
            user.realm.host,
            {"Authorization": f"Basic {auth}"},
            {"Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}"},
        )
    finally:
        session.delete()


def create_and_claim(
    proxy: PrivateReadProxy, owner: Actor, peer: Actor, name: str, contents: bytes
) -> Uploaded:
    from zerver.models import Attachment

    boundary = "zulip-private-attachment-flow"
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + contents
        + f"\r\n--{boundary}--\r\n".encode()
    )
    upload, upstream = proxy.observe(
        "POST",
        owner.host,
        {**owner.basic, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        "/api/v1/user_uploads",
        body=body,
    )
    assert (upload[0], upstream) == (200, proxy.django_address), "upload stays on Django"
    payload = json.loads(upload[1])
    assert payload["filename"] == name
    url = urlsplit(payload["url"])
    assert not (url.scheme or url.netloc or url.query or url.fragment)
    assert url.path.startswith("/user_uploads/") and url.path == payload["uri"]

    sent, upstream = proxy.observe(
        "POST",
        owner.host,
        {**owner.basic, "Content-Type": "application/x-www-form-urlencoded"},
        "/api/v1/messages",
        body=urlencode(
            {
                "type": "direct",
                "to": json.dumps([peer.user_id]),
                "content": f"[{name}]({url.path})",
            }
        ),
    )
    assert (sent[0], upstream) == (200, proxy.django_address), "message send stays on Django"
    message_id = json.loads(sent[1])["id"]
    attachment = Attachment.objects.get(owner_id=owner.user_id, file_name=name)
    assert attachment.size == len(contents)
    assert list(attachment.messages.values_list("id", flat=True)) == [message_id]
    assert url.path == f"/user_uploads/{attachment.path_id}"
    return Uploaded(attachment.id, name, attachment.path_id, url.path, contents, message_id)


def verify_download(proxy: PrivateReadProxy, reader: Actor, upload: Uploaded) -> None:
    reply, upstream = proxy.observe("GET", reader.host, reader.web, upload.url)
    assert upstream == proxy.django_address, "download stays on Django"
    assert reply[0] == 200 and reply[1] == upload.contents, "original bytes"
    content_type = reply[2]["Content-Type"]
    assert content_type is not None and content_type.startswith("text/plain")


class AttachmentEntry(TypedDict):
    id: int
    name: str
    path_id: str
    size: int
    create_time: int
    message_ids: list[int]


class AttachmentList(TypedDict):
    attachments: list[AttachmentEntry]
    upload_space_used: int


def normalized(reply: ReadResult) -> AttachmentList:
    payload = cast(AttachmentList, json.loads(reply[1]))
    for upload in payload["attachments"]:
        upload["message_ids"].sort()
    payload["attachments"].sort(key=lambda upload: upload["id"])
    return payload


def lists(
    proxy: PrivateReadProxy,
    owner: Actor,
    uploads: tuple[Uploaded, ...],
    expected_bytes: int,
    upstream: str,
) -> list[ReadResult]:
    results = []
    for endpoint, headers in ((API, owner.basic), (WEB, owner.web)):
        reply, routed = proxy.observe("GET", owner.host, headers, endpoint)
        assert reply[0] == 200 and routed == upstream, "attachment list upstream"
        payload = normalized(reply)
        assert payload["upload_space_used"] == expected_bytes, "organization usage"
        assert [
            (item["id"], item["name"], item["path_id"], item["size"], item["message_ids"])
            for item in payload["attachments"]
        ] == [
            (upload.id, upload.name, upload.path_id, len(upload.contents), [upload.message_id])
            for upload in uploads
        ], "owner and claimed message list"
        results.append(reply)
        head, routed = proxy.observe("HEAD", owner.host, headers, endpoint)
        assert head[0] == 200 and head[1] == b"" and routed == upstream, "empty HEAD"
        results.append(head)
    return results


def compare(left: list[ReadResult], right: list[ReadResult]) -> None:
    for index, (result, reference) in enumerate(zip(left, right, strict=True)):
        assert result[0] == reference[0]
        if index % 2 == 0:
            assert normalized(result) == normalized(reference)
        else:
            assert result[1] == reference[1]
        compare_read_headers(result, reference, "attachment list/HEAD")


def exercise(proxy: PrivateReadProxy, directory: Path, owner: Actor, peer: Actor) -> None:
    def ready(expected: str) -> bool:
        reply, upstream = proxy.observe("GET", owner.host, owner.basic, API)
        return reply[0] == 200 and upstream == expected

    wait_until(lambda: ready(proxy.django_address))
    proxy.assert_untrusted_tls_rejected(owner.host, API)
    first = create_and_claim(proxy, owner, peer, "first.txt", b"First synthetic attachment.\n")
    by_django = lists(proxy, owner, (first,), len(first.contents), proxy.django_address)
    verify_download(proxy, owner, first)

    secret = directory / "synthetic-secret"
    secret.write_text(settings.SECRET_KEY)
    secret.chmod(0o600)
    with rust_http_service(
        {
            "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
            "ZULIP_RUST_DJANGO_AUTH_BACKENDS": json.dumps(settings.AUTHENTICATION_BACKENDS),
            "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
        }
    ) as rust_port:
        rust = f"127.0.0.1:{rust_port}"
        proxy.enable_routes((ROUTES,), rust_port, upstreams_per_route=2)
        wait_until(lambda: ready(rust))
        compare(lists(proxy, owner, (first,), len(first.contents), rust), by_django)
        second = create_and_claim(
            proxy, owner, peer, "second.txt", b"Second synthetic attachment.\n"
        )
        usage = len(first.contents) + len(second.contents)
        by_rust = lists(proxy, owner, (first, second), usage, rust)
        lists(proxy, peer, (), usage, rust)
        verify_download(proxy, peer, first)

    for endpoint, headers in ((API, owner.basic), (WEB, owner.web)):
        failed, upstream = proxy.observe("GET", owner.host, headers, endpoint)
        assert failed[0] == 502 and upstream == rust
        assert json.loads(failed[1]) == json.loads(proxy.error_body())
    verify_download(proxy, owner, second)
    proxy.disable_route(ROUTES)
    wait_until(lambda: ready(proxy.django_address))
    compare(lists(proxy, owner, (first, second), usage, proxy.django_address), by_rust)
    lists(proxy, peer, (), usage, proxy.django_address)
    verify_download(proxy, peer, second)
    print(
        "Two real synthetic uploads and message claims stayed on Django; "
        "both lists changed on Rust; recipient downloads, HEAD and Django rollback passed. "
        f"{proxy.requests} private requests including readiness."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    for tool in ("nginx", "uwsgi", "venv", "openssl"):
        parser.add_argument(f"--{tool}", type=Path, required=True)
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("private loopback only")
    if any(name.startswith("ZULIP_RUST_") and name != "ZULIP_RUST_HTTP_BIN" for name in os.environ):
        raise RuntimeError("use a scrubbed environment with only ZULIP_RUST_HTTP_BIN configured")
    validate_fixture(root)
    django.setup()
    verify_private_database()

    from zerver.models import Attachment, UserProfile

    assert settings.DEVELOPMENT and settings.LOCAL_UPLOADS_DIR and settings.LOCAL_FILES_DIR
    storage = Path(settings.LOCAL_UPLOADS_DIR).resolve(strict=True)
    assert storage.is_relative_to(root) and storage != root
    assert Path(settings.LOCAL_FILES_DIR).resolve().is_relative_to(storage)
    assert not any(storage.iterdir()), "use a new empty private storage root"
    owner = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    peer = UserProfile.objects.get(realm=owner.realm, delivery_email="cordelia@zulip.com")
    assert not Attachment.objects.filter(realm=owner.realm).exists()

    with (
        tempfile.TemporaryDirectory(prefix="attachment-flow-", dir=root) as tmp,
        actor(owner) as owner_actor,
        actor(peer) as peer_actor,
    ):
        directory = Path(tmp)
        with private_read_proxy(
            ROOT,
            directory,
            args.nginx,
            args.uwsgi,
            args.venv,
            (MAP,),
            openssl_bin=args.openssl,
            browser_login=True,
        ) as proxy:
            exercise(proxy, directory, owner_actor, peer_actor)


if __name__ == "__main__":
    main()
