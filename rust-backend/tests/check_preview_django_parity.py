# ruff: noqa: INP001
"""Render real previews and delivery payloads without network access."""

import argparse
import socket
from collections.abc import Callable
from functools import partial
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import django
import psycopg2
from check_fenced_django_parity import check_database
from django.db import connection, transaction
from django.test import override_settings
from psycopg2.extensions import parse_dsn

if TYPE_CHECKING:
    from zerver.models import Realm


def check_previews(realm: "Realm", backend: object) -> int:
    from zerver.lib.camo import get_camo_url
    from zerver.lib.markdown import do_convert, native_preview_urls
    from zerver.lib.url_preview.types import UrlEmbedData

    youtube = "https://youtu.be/clip_01"
    vimeo = "https://vimeo.com/123"
    github = "https://github.com/owner/repo/blob/main/sample.png"
    wiki = "https://fa.wikipedia.org/wiki/File:photo.png"
    linx = "https://linx.li/example.png"
    dropbox_image = "https://dropbox.com/scl/fi/demo/sample.jpg?raw=0&key=one&key=two"
    dropbox_video = "https://dropbox.com/scl/fi/demo/video.mp4?dl=0"
    dropbox_album = "https://dropbox.com/scl/fo/demo/album"
    dropbox_document = "https://dropbox.com/scl/fi/demo/document.pdf"
    tests = (
        ("youtube", youtube, None, 'data-id="clip_01"', {youtube}),
        (
            "vimeo",
            vimeo,
            {
                vimeo: UrlEmbedData(
                    type="image",
                    title="Local title",
                    description="Test metadata",
                    image="https://example.test/thumbnail.png",
                )
            },
            "Vimeo - Local title",
            set(),
        ),
        (
            "github",
            github,
            None,
            get_camo_url("https://raw.githubusercontent.com/owner/repo/main/sample.png"),
            set(),
        ),
        ("wiki", wiki, None, "/wiki/Special:FilePath/File:photo.png", set()),
        ("linx", linx, None, "https://linx.li/s/example.png", set()),
        (
            "dropbox-image",
            dropbox_image,
            None,
            "raw=1&amp;key=two",
            set(),
        ),
        ("dropbox-video", dropbox_video, None, "dl=0&amp;raw=1", set()),
        ("dropbox-album", dropbox_album, None, 'href="' + dropbox_album + '"', {dropbox_album}),
        (
            "dropbox-document",
            dropbox_document,
            None,
            'href="' + dropbox_document + '"',
            {dropbox_document},
        ),
        (
            "mixed-previews",
            f"{youtube}\n\n{github}\n\n{youtube}",
            None,
            'data-id="clip_01"',
            {youtube},
        ),
        ("local-link", "[Local](/help/)", None, 'href="/help/"', set()),
    )

    with override_settings(INLINE_IMAGE_PREVIEW=True, INLINE_URL_EMBED_PREVIEW=True):
        for label, content, metadata, html_part, expected_links in tests:
            for message_realm in (None, realm):
                for no_previews in (False, True):
                    render = partial(
                        do_convert,
                        content,
                        message_realm=message_realm,
                        url_embed_data=metadata,
                        no_previews=no_previews,
                    )
                    with patch.object(native_preview_urls, "_backend", None):
                        reference = render()
                    with patch.object(native_preview_urls, "_backend", backend):
                        migrated = render()
                    if reference != migrated:
                        raise AssertionError(
                            f"preview parity failed for {label}, realm={message_realm is not None}"
                        )
                    if not no_previews and (
                        html_part not in migrated.rendered_content
                        or migrated.links_for_preview != expected_links
                    ):
                        raise AssertionError(f"fixture did not exercise expected preview: {label}")
    return len(tests) * 4


def check_mobile_delivery(realm: "Realm", backend: object) -> int:
    from zerver.lib.markdown import do_convert, reply_links
    from zerver.lib.message_cache import MessageDict
    from zerver.models import Recipient, UserProfile
    from zerver.tornado.event_queue import ClientDescriptor, EventQueue

    path = "#narrow/dm/12-test/near/31"
    content = f'[Sender | سلام &amp; متن]({path} "zulip-message-reply")'
    rich = do_convert(content, message_realm=realm, no_previews=True).rendered_content
    expected_plain = f'<p><a href="{path}">Sender · سلام &amp; متن</a></p>'
    if 'class="message-reply-link"' not in rich:
        raise AssertionError("production renderer did not generate a rich reply link")

    message = {
        "recipient_type": Recipient.DIRECT_MESSAGE_GROUP,
        "recipient_type_id": 1,
        "content": content,
        "rendered_content": rich,
        "sender_id": 12,
        "sender_is_mirror_dummy": False,
        "sender_realm_id": realm.id,
        "sender_delivery_email": "sender@example.test",
        "sender_avatar_source": UserProfile.AVATAR_FROM_GRAVATAR,
        "sender_avatar_version": 1,
        "sender_email_address_visibility": UserProfile.EMAIL_ADDRESS_VISIBILITY_EVERYONE,
        "match_content": rich,
        "edit_history": [{"prev_rendered_content": rich}],
    }

    def prepare(name: str, apply_markdown: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        queue = EventQueue("isolated-reply-parity")
        queue.push(
            {
                "type": "message",
                "message": {
                    "content_type": "text/html" if apply_markdown else "text/x-markdown",
                    "content": rich if apply_markdown else content,
                },
            },
        )
        queue.push(
            {
                "type": "update_message",
                "rendered_content": rich,
                "orig_rendered_content": rich,
            }
        )
        queue.push({"type": "url_embed_data", "rendered_content": rich})
        client = ClientDescriptor(
            user_profile_id=12,
            realm_id=realm.id,
            event_queue=queue,
            event_types=None,
            client_type_name=name,
            apply_markdown=apply_markdown,
            client_gravatar=False,
            slim_presence=False,
            all_public_streams=False,
            idle_queue_timeout=None,
            narrow=[],
            bulk_message_deletion=False,
            stream_typing_notifications=False,
            pronouns_field_type_supported=False,
            linkifier_url_template=False,
            user_list_incomplete=False,
            include_deactivated_groups=False,
            archived_channels=False,
            empty_topic_name=True,
            simplified_presence_events=False,
            individual_emoji_changes=False,
        )
        events = client.events_for_client()
        # The production fetch path uses finalize_payload to build its response.
        payload = MessageDict.finalize_payload(
            message,
            apply_markdown=apply_markdown,
            client_gravatar=False,
            allow_empty_topic_name=True,
            can_access_sender=True,
            realm_host=realm.host,
            plain_reply_links=reply_links.needs_plain_reply_links(name),
        )
        if queue.contents()[1]["rendered_content"] != rich or message["rendered_content"] != rich:
            raise AssertionError("compatibility formatting mutated shared rendered HTML")
        return events, payload

    names = ("ZulipFlutter", "ZulipMobile", "ZulipAndroid", "ZulipiOS", "website", "ZulipDesktop")
    for name in names:
        for apply_markdown in (False, True):
            with patch.object(reply_links, "_rust_reply_links", None):
                expected = prepare(name, apply_markdown)
            with patch.object(reply_links, "_rust_reply_links", backend):
                actual = prepare(name, apply_markdown)
            if expected != actual:
                raise AssertionError(f"reply payload parity failed for {name}")
            events, payload = actual
            expected_html = rich if name in ("website", "ZulipDesktop") else expected_plain
            expected_content = expected_html if apply_markdown else content
            if (
                events[0]["message"]["content"] != expected_content
                or events[1]["orig_rendered_content"] != expected_html
                or events[1]["rendered_content"] != expected_html
                or events[2]["rendered_content"] != expected_html
                or payload["content"] != expected_content
                or payload["match_content"] != expected_html
                or payload["edit_history"][0]["prev_rendered_content"] != expected_html
            ):
                raise AssertionError(f"reply fixture did not exercise production paths for {name}")
    return len(names) * 2


def run() -> tuple[int, int]:
    from zerver.actions.create_realm import set_default_for_realm_permission_group_settings
    from zerver.lib.user_groups import create_system_user_groups_for_realm
    from zerver.models import Realm

    backend = import_module("_zulip_backend")
    with transaction.atomic():
        realm = Realm(
            name="Disposable Preview Parity",
            string_id="isolated-preview-parity",
            inline_url_embed_preview=True,
            inline_image_preview=True,
        )
        for setting_name in Realm.REALM_PERMISSION_GROUP_SETTINGS:
            setattr(realm, setting_name + "_id", -1)
        realm.save()
        create_system_user_groups_for_realm(realm)
        set_default_for_realm_permission_group_settings(realm)
        counts = check_previews(realm, backend), check_mobile_delivery(realm, backend)
        transaction.set_rollback(True)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-directory", required=True, type=Path)
    args = parser.parse_args()
    socket_directory = args.socket_directory.resolve(strict=True)
    original_connect: Callable[..., Any] = psycopg2.connect

    def blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("isolated preview check forbids network access")

    def isolated_connect(dsn: str | None = None, *args: Any, **kwargs: Any) -> Any:
        parameters = parse_dsn(dsn or "") | kwargs
        if parameters.get("host") != str(socket_directory) or parameters.get("hostaddr"):
            raise RuntimeError("a connection outside the private PostgreSQL socket was requested")
        return original_connect(dsn, *args, **kwargs)

    with (
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(psycopg2, "connect", isolated_connect),
    ):
        check_database(socket_directory)
        django.setup()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT inet_server_addr(), current_database(), current_setting('listen_addresses')"
            )
            if cursor.fetchone() != (None, "zulip_test", ""):
                raise RuntimeError("expected the disposable Unix socket test database")
        previews, mobile = run()
    print(f"Matching Zulip preview HTML and metadata: {previews} cases")
    print(f"Matching Tornado and message fetch delivery payloads: {mobile} cases")


if __name__ == "__main__":
    main()
