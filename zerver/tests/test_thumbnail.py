import re
from dataclasses import asdict
from importlib import import_module
from io import BytesIO, StringIO
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import orjson
import pyvips
from django.conf import settings
from django.http import HttpRequest, StreamingHttpResponse
from django.test import override_settings

from zerver.lib import native_thumbnail_status
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import (
    consume_response,
    get_test_image_file,
    ratelimit_rule,
    read_test_image_file,
)
from zerver.lib.thumbnail import (
    AttachmentData,
    BadImageError,
    BaseThumbnailFormat,
    StoredThumbnailFormat,
    ThumbnailFormat,
    get_image_thumbnail_path,
    get_transcoded_format,
    manifest_and_get_user_upload_previews,
    missing_thumbnails,
    plan_missing_thumbnails,
    resize_emoji,
    split_thumbnail_path,
)
from zerver.lib.upload import (
    all_message_attachments,
    attachment_source,
    create_attachment,
    generate_message_upload_path,
    save_attachment_contents,
    store_message_attachment,
)
from zerver.models import Attachment, ImageAttachment
from zerver.views.upload import closest_thumbnail_format, stored_thumbnail_variant
from zerver.worker.thumbnail import ensure_thumbnails


class ThumbnailRedirectEndpointTest(ZulipTestCase):
    """Tests for the legacy /thumbnail endpoint."""

    def test_thumbnail_upload_redirect(self) -> None:
        self.login("hamlet")
        fp = StringIO("zulip!")
        fp.name = "zulip.jpeg"

        result = self.client_post("/json/user_uploads", {"file": fp})
        self.assert_json_success(result)
        json = orjson.loads(result.content)
        self.assertIn("uri", json)
        self.assertIn("url", json)
        url = json["url"]
        self.assertEqual(json["uri"], url)
        base = "/user_uploads/"
        self.assertEqual(base, url[: len(base)])

        result = self.client_get("/thumbnail", {"url": url.removeprefix("/"), "size": "full"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.getvalue(), b"zulip!")

        self.login("iago")
        result = self.client_get("/thumbnail", {"url": url.removeprefix("/"), "size": "full"})
        self.assertEqual(result.status_code, 403, result)
        self.assert_in_response("You are not authorized to view this file.", result)

    def test_thumbnail_external_redirect(self) -> None:
        url = "https://www.google.com/images/srpr/logo4w.png"
        result = self.client_get("/thumbnail", {"url": url, "size": "full"})
        self.assertEqual(result.status_code, 403)

        url = "http://www.google.com/images/srpr/logo4w.png"
        result = self.client_get("/thumbnail", {"url": url, "size": "full"})
        self.assertEqual(result.status_code, 403)

        url = "//www.google.com/images/srpr/logo4w.png"
        result = self.client_get("/thumbnail", {"url": url, "size": "full"})
        self.assertEqual(result.status_code, 403)

    @override_settings(RATE_LIMITING=True)
    def test_thumbnail_redirect_for_spectator(self) -> None:
        self.login("hamlet")
        fp = StringIO("zulip!")
        fp.name = "zulip.jpeg"

        result = self.client_post("/json/user_uploads", {"file": fp})
        self.assert_json_success(result)
        json = orjson.loads(result.content)
        url = json["url"]
        self.assertEqual(json["uri"], url)

        with ratelimit_rule(86400, 1000, domain="spectator_attachment_access_by_file"):
            # Deny file access for non-web-public stream
            self.subscribe(self.example_user("hamlet"), "Denmark")
            host = self.example_user("hamlet").realm.host
            body = f"First message ...[zulip.txt](http://{host}" + url + ")"
            self.send_stream_message(self.example_user("hamlet"), "Denmark", body, "test")

            self.logout()
            response = self.client_get("/thumbnail", {"url": url.removeprefix("/"), "size": "full"})
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith("/accounts/login/?next="))

            # Allow file access for web-public stream
            self.login("hamlet")
            self.make_stream("web-public-stream", is_web_public=True)
            self.subscribe(self.example_user("hamlet"), "web-public-stream")
            body = f"First message ...[zulip.txt](http://{host}" + url + ")"
            self.send_stream_message(self.example_user("hamlet"), "web-public-stream", body, "test")

            self.logout()
            response = self.client_get("/thumbnail", {"url": url.removeprefix("/"), "size": "full"})
            self.assertEqual(response.status_code, 200)
            consume_response(response)

        # Deny file access since rate limited
        with ratelimit_rule(86400, 0, domain="spectator_attachment_access_by_file"):
            response = self.client_get("/thumbnail", {"url": url.removeprefix("/"), "size": "full"})
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith("/accounts/login/?next="))

        # Deny random file access
        response = self.client_get(
            "/thumbnail",
            {
                "url": "user_uploads/2/71/QYB7LA-ULMYEad-QfLMxmI2e/zulip-non-existent.txt",
                "size": "full",
            },
        )
        self.assertEqual(response.status_code, 404)


class ThumbnailEmojiTest(ZulipTestCase):
    def animated_test(self, filename: str) -> None:
        animated_unequal_img_data = read_test_image_file(filename)
        original_image = pyvips.Image.new_from_buffer(animated_unequal_img_data, "n=-1")
        resized_img_data, still_img_data = resize_emoji(
            animated_unequal_img_data, filename, size=50
        )
        assert still_img_data is not None
        emoji_image = pyvips.Image.new_from_buffer(resized_img_data, "n=-1")
        self.assertEqual(emoji_image.get("vips-loader"), "gifload_buffer")
        self.assertEqual(emoji_image.get_n_pages(), original_image.get_n_pages())
        self.assertEqual(emoji_image.get("page-height"), 50)
        self.assertEqual(emoji_image.height, 150)
        self.assertEqual(emoji_image.width, 50)

        still_image = pyvips.Image.new_from_buffer(still_img_data, "")
        self.assertEqual(still_image.get("vips-loader"), "pngload_buffer")
        self.assertEqual(still_image.get_n_pages(), 1)
        self.assertEqual(still_image.height, 50)
        self.assertEqual(still_image.width, 50)

    def test_resize_animated_square(self) -> None:
        """An animated image which is square"""
        self.animated_test("animated_large_img.gif")

    def test_resize_animated_emoji(self) -> None:
        """An animated image which is not square"""
        self.animated_test("animated_unequal_img.gif")

    def test_resize_corrupt_emoji(self) -> None:
        corrupted_img_data = read_test_image_file("corrupt.gif")
        with self.assertRaises(BadImageError):
            resize_emoji(corrupted_img_data, "corrupt.gif")

    def test_resize_too_many_pixels(self) -> None:
        """An image file with too many pixels is not resized"""
        bomb_img_data = read_test_image_file("bomb.png")
        with self.assertRaises(BadImageError):
            resize_emoji(bomb_img_data, "bomb.png", size=50)

    def test_animated_resize_too_many_pixels(self) -> None:
        with patch("zerver.lib.thumbnail.IMAGE_BOMB_TOTAL_PIXELS", 100000):
            # This image is 256 * 256 with 3 frames, so 196k pixels.
            # When resizing emoji, we want to show the whole
            # animation, so every pixel on every frame counts
            animated_large_img_data = read_test_image_file("animated_large_img.gif")
            with self.assertRaises(BadImageError):
                resize_emoji(animated_large_img_data, "animated_large_img.gif", size=50)

    def test_resize_still_gif(self) -> None:
        """A non-animated square emoji resize"""
        still_large_img_data = read_test_image_file("still_large_img.gif")
        resized_img_data, no_still_data = resize_emoji(
            still_large_img_data, "still_large_img.gif", size=50
        )
        emoji_image = pyvips.Image.new_from_buffer(resized_img_data, "n=-1")
        self.assertEqual(emoji_image.get("vips-loader"), "gifload_buffer")
        self.assertEqual(emoji_image.height, 50)
        self.assertEqual(emoji_image.width, 50)
        self.assertEqual(emoji_image.get_n_pages(), 1)
        assert no_still_data is None

    def test_resize_still_jpg(self) -> None:
        """A non-animatatable format resize"""
        still_large_img_data = read_test_image_file("img.jpg")
        resized_img_data, no_still_data = resize_emoji(still_large_img_data, "img.jpg", size=50)
        emoji_image = pyvips.Image.new_from_buffer(resized_img_data, "")
        self.assertEqual(emoji_image.get("vips-loader"), "jpegload_buffer")
        self.assertEqual(emoji_image.height, 50)
        self.assertEqual(emoji_image.width, 50)
        self.assertEqual(emoji_image.get_n_pages(), 1)
        assert no_still_data is None

    def test_non_image_format_wrong_content_type(self) -> None:
        """A file that is not an image"""
        non_img_data = read_test_image_file("text.txt")
        with self.assertRaises(BadImageError):
            resize_emoji(non_img_data, "text.png", size=50)


class ThumbnailClassesTest(ZulipTestCase):
    def test_class_equivalence(self) -> None:
        self.assertNotEqual(
            ThumbnailFormat("webp", 150, 100, animated=True, opts="Q=90"),
            "150x100-anim.webp",
        )

        self.assertEqual(
            ThumbnailFormat("webp", 150, 100, animated=True, opts="Q=90"),
            ThumbnailFormat("webp", 150, 100, animated=True, opts="Q=10"),
        )

        self.assertEqual(
            ThumbnailFormat("webp", 150, 100, animated=True, opts="Q=90"),
            BaseThumbnailFormat("webp", 150, 100, animated=True),
        )

        self.assertNotEqual(
            ThumbnailFormat("jpeg", 150, 100, animated=True, opts="Q=90"),
            ThumbnailFormat("webp", 150, 100, animated=True, opts="Q=90"),
        )

        self.assertNotEqual(
            ThumbnailFormat("webp", 300, 100, animated=True, opts="Q=90"),
            ThumbnailFormat("webp", 150, 100, animated=True, opts="Q=90"),
        )

        self.assertNotEqual(
            ThumbnailFormat("webp", 150, 100, animated=False, opts="Q=90"),
            ThumbnailFormat("webp", 150, 100, animated=True, opts="Q=90"),
        )

        # We can compare stored thumbnails, with much more metadata,
        # to the thumbnail formats that spec how they are generated
        self.assertEqual(
            ThumbnailFormat("webp", 150, 100, animated=False, opts="Q=90"),
            StoredThumbnailFormat(
                "webp",
                150,
                100,
                animated=False,
                content_type="image/webp",
                width=120,
                height=100,
                byte_size=123,
            ),
        )

        # But differences in the base four properties mean they are not equal
        self.assertNotEqual(
            ThumbnailFormat("webp", 150, 100, animated=False, opts="Q=90"),
            StoredThumbnailFormat(
                "webp",
                150,
                100,
                animated=True,  # Note this change
                content_type="image/webp",
                width=120,
                height=100,
                byte_size=123,
            ),
        )

    def test_stringification(self) -> None:
        # These formats need to be stable, since they are written into URLs in the messages.
        self.assertEqual(
            str(ThumbnailFormat("webp", 150, 100, animated=False)),
            "150x100.webp",
        )

        self.assertEqual(
            str(ThumbnailFormat("webp", 150, 100, animated=True)),
            "150x100-anim.webp",
        )

        # And they should round-trip into BaseThumbnailFormat, losing the opts= which we do not serialize
        thumb_format = ThumbnailFormat("webp", 150, 100, animated=True, opts="Q=90")
        self.assertEqual(thumb_format.extension, "webp")
        self.assertEqual(thumb_format.max_width, 150)
        self.assertEqual(thumb_format.max_height, 100)
        self.assertEqual(thumb_format.animated, True)

        round_trip = BaseThumbnailFormat.from_string(str(thumb_format))
        assert round_trip is not None
        self.assertEqual(thumb_format, round_trip)
        self.assertEqual(round_trip.extension, "webp")
        self.assertEqual(round_trip.max_width, 150)
        self.assertEqual(round_trip.max_height, 100)
        self.assertEqual(round_trip.animated, True)

        self.assertIsNone(BaseThumbnailFormat.from_string("bad.webp"))


class TestStoreThumbnail(ZulipTestCase):
    def test_upload_image(self) -> None:
        assert settings.LOCAL_FILES_DIR
        self.login_user(self.example_user("hamlet"))

        with (
            self.thumbnail_formats(ThumbnailFormat("webp", 100, 75, animated=True)),
            self.captureOnCommitCallbacks(execute=True),
        ):
            with get_test_image_file("animated_unequal_img.gif") as image_file:
                response = self.assert_json_success(
                    self.client_post("/json/user_uploads", {"file": image_file})
                )
            path_id = re.sub(r"/user_uploads/", "", response["url"])
            self.assertEqual(Attachment.objects.filter(path_id=path_id).count(), 1)

            image_attachment = ImageAttachment.objects.get(path_id=path_id)
            self.assertEqual(image_attachment.original_height_px, 56)
            self.assertEqual(image_attachment.original_width_px, 128)
            self.assertEqual(image_attachment.frames, 3)
            self.assertEqual(image_attachment.thumbnail_metadata, [])

            self.assertEqual(
                [r[0] for r in all_message_attachments(include_thumbnails=True)],
                [path_id],
            )

            # The worker triggers when we exit this block and call the pending callbacks
        image_attachment = ImageAttachment.objects.get(path_id=path_id)
        self.assert_length(image_attachment.thumbnail_metadata, 1)
        generated_thumbnail = StoredThumbnailFormat(**image_attachment.thumbnail_metadata[0])

        self.assertEqual(str(generated_thumbnail), "100x75-anim.webp")
        self.assertEqual(generated_thumbnail.animated, True)
        self.assertEqual(generated_thumbnail.width, 100)
        self.assertEqual(generated_thumbnail.height, 44)
        self.assertEqual(generated_thumbnail.content_type, "image/webp")
        self.assertGreater(generated_thumbnail.byte_size, 200)
        self.assertLess(generated_thumbnail.byte_size, 2 * 1024)

        self.assertEqual(
            get_image_thumbnail_path(image_attachment, generated_thumbnail),
            f"thumbnail/{path_id}/100x75-anim.webp",
        )
        parsed_path = split_thumbnail_path(f"thumbnail/{path_id}/100x75-anim.webp")
        self.assertEqual(parsed_path[0], path_id)
        self.assertIsInstance(parsed_path[1], BaseThumbnailFormat)
        self.assertEqual(str(parsed_path[1]), str(generated_thumbnail))

        self.assertEqual(
            sorted([r[0] for r in all_message_attachments(include_thumbnails=True)]),
            sorted([path_id, f"thumbnail/{path_id}/100x75-anim.webp"]),
        )

        with BytesIO() as fh:
            save_attachment_contents(f"thumbnail/{path_id}/100x75-anim.webp", fh)
            thumbnailed_bytes = fh.getvalue()
        with pyvips.Image.new_from_buffer(thumbnailed_bytes, "") as thumbnailed_image:
            self.assertEqual(thumbnailed_image.get("vips-loader"), "webpload_buffer")
            self.assertEqual(thumbnailed_image.width, 100)
            self.assertEqual(thumbnailed_image.height, 44)
            self.assertEqual(thumbnailed_image.get_n_pages(), 2)

        with self.thumbnail_formats(ThumbnailFormat("webp", 100, 75, animated=True)):
            self.assertEqual(ensure_thumbnails(image_attachment).generated_thumbnail_count, 0)
        self.assert_length(image_attachment.thumbnail_metadata, 1)

        with self.thumbnail_formats(ThumbnailFormat("webp", 150, 100, opts="Q=90", animated=False)):
            self.assertEqual(ensure_thumbnails(image_attachment).generated_thumbnail_count, 1)
        self.assert_length(image_attachment.thumbnail_metadata, 2)

        bigger_thumbnail = StoredThumbnailFormat(**image_attachment.thumbnail_metadata[1])

        self.assertEqual(str(bigger_thumbnail), "150x100.webp")
        self.assertEqual(bigger_thumbnail.animated, False)
        # We don't scale up, so these are the original dimensions
        self.assertEqual(bigger_thumbnail.width, 128)
        self.assertEqual(bigger_thumbnail.height, 56)
        self.assertEqual(bigger_thumbnail.content_type, "image/webp")
        self.assertGreater(bigger_thumbnail.byte_size, 200)
        self.assertLess(bigger_thumbnail.byte_size, 2 * 1024)

        with BytesIO() as fh:
            save_attachment_contents(f"thumbnail/{path_id}/150x100.webp", fh)
            thumbnailed_bytes = fh.getvalue()
        with pyvips.Image.new_from_buffer(thumbnailed_bytes, "") as thumbnailed_image:
            self.assertEqual(thumbnailed_image.get("vips-loader"), "webpload_buffer")
            self.assertEqual(thumbnailed_image.width, 128)
            self.assertEqual(thumbnailed_image.height, 56)
            self.assertEqual(thumbnailed_image.get_n_pages(), 1)

        self.assertEqual(
            sorted([r[0] for r in all_message_attachments(include_thumbnails=True)]),
            sorted(
                [
                    path_id,
                    f"thumbnail/{path_id}/100x75-anim.webp",
                    f"thumbnail/{path_id}/150x100.webp",
                ]
            ),
        )

    def test_animated_resize_partial_frames(self) -> None:
        self.login_user(self.example_user("hamlet"))
        with self.thumbnail_formats(ThumbnailFormat("webp", 100, 75, animated=True)):
            with (
                patch("zerver.lib.thumbnail.IMAGE_MAX_ANIMATED_PIXELS", 100000),
                patch("zerver.worker.thumbnail.IMAGE_MAX_ANIMATED_PIXELS", 100000),
                get_test_image_file("animated_many_frames.gif") as image_file,
            ):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.assert_json_success(
                        self.client_post("/json/user_uploads", {"file": image_file})
                    )
                    path_id = re.sub(r"/user_uploads/", "", response["url"])
                    self.assertEqual(Attachment.objects.filter(path_id=path_id).count(), 1)

                    image_attachment = ImageAttachment.objects.get(path_id=path_id)
                    self.assertEqual(image_attachment.original_height_px, 100)
                    self.assertEqual(image_attachment.original_width_px, 200)
                    # Metadata shows the total frame count
                    self.assertEqual(image_attachment.frames, 69)
                # Exit the captureOnCommitCallbacks block and run thumbnailing
                with BytesIO() as fh:
                    save_attachment_contents(f"thumbnail/{path_id}/100x75-anim.webp", fh)
                    thumbnailed_bytes = fh.getvalue()
                with pyvips.Image.new_from_buffer(thumbnailed_bytes, "") as thumbnailed_image:
                    self.assertEqual(thumbnailed_image.get("vips-loader"), "webpload_buffer")
                    self.assertEqual(thumbnailed_image.width, 100)
                    self.assertEqual(thumbnailed_image.height, 50)
                    # IMAGE_MAX_ANIMATED_PIXELS means that we only
                    # thumbnail the first 5 frames (100k / (100 * 200))
                    self.assertEqual(thumbnailed_image.get_n_pages(), 5)

            # If we have higher IMAGE_MAX_ANIMATED_PIXELS then we thumbnail all frames
            with (
                patch("zerver.lib.thumbnail.IMAGE_MAX_ANIMATED_PIXELS", 100 * 200 * 70),
                patch("zerver.worker.thumbnail.IMAGE_MAX_ANIMATED_PIXELS", 100 * 200 * 70),
                get_test_image_file("animated_many_frames.gif") as image_file,
            ):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.assert_json_success(
                        self.client_post("/json/user_uploads", {"file": image_file})
                    )
                    path_id = re.sub(r"/user_uploads/", "", response["url"])
                    self.assertEqual(Attachment.objects.filter(path_id=path_id).count(), 1)
                    self.assertEqual(ImageAttachment.objects.filter(path_id=path_id).count(), 1)
                with BytesIO() as fh:
                    save_attachment_contents(f"thumbnail/{path_id}/100x75-anim.webp", fh)
                    thumbnailed_bytes = fh.getvalue()
                with pyvips.Image.new_from_buffer(thumbnailed_bytes, "") as thumbnailed_image:
                    self.assertEqual(thumbnailed_image.get_n_pages(), 69)

            # If IMAGE_MAX_ANIMATED_PIXELS isn't enough to be able to
            # fit 3 frames in, then we don't display a thumbnail at
            # all.
            with (
                patch("zerver.lib.thumbnail.IMAGE_MAX_ANIMATED_PIXELS", 100 * 200),
                patch("zerver.worker.thumbnail.IMAGE_MAX_ANIMATED_PIXELS", 100 * 200),
                get_test_image_file("animated_many_frames.gif") as image_file,
            ):
                response = self.assert_json_success(
                    self.client_post("/json/user_uploads", {"file": image_file})
                )
                path_id = re.sub(r"/user_uploads/", "", response["url"])
                self.assertEqual(Attachment.objects.filter(path_id=path_id).count(), 1)
                self.assertEqual(ImageAttachment.objects.filter(path_id=path_id).count(), 0)

    def test_image_orientation(self) -> None:
        self.login_user(self.example_user("hamlet"))

        with (
            self.thumbnail_formats(ThumbnailFormat("webp", 100, 75, animated=False)),
            self.captureOnCommitCallbacks(execute=True),
        ):
            with get_test_image_file("orientation.jpg") as image_file:
                response = self.assert_json_success(
                    self.client_post("/json/user_uploads", {"file": image_file})
                )
            path_id = re.sub(r"/user_uploads/", "", response["url"])
            self.assertEqual(Attachment.objects.filter(path_id=path_id).count(), 1)

            image_attachment = ImageAttachment.objects.get(path_id=path_id)
            # The bytes in this image are 100 wide, and 600 tall --
            # however, it has EXIF orientation information which says
            # to rotate it 270 degrees counter-clockwise.
            self.assertEqual(image_attachment.original_height_px, 100)
            self.assertEqual(image_attachment.original_width_px, 600)

            # The worker triggers when we exit this block and call the pending callbacks
        image_attachment = ImageAttachment.objects.get(path_id=path_id)
        self.assert_length(image_attachment.thumbnail_metadata, 1)
        generated_thumbnail = StoredThumbnailFormat(**image_attachment.thumbnail_metadata[0])

        # The uploaded original content is technically "tall", not "wide", with a 270 CCW rotation set.
        with BytesIO() as fh:
            save_attachment_contents(path_id, fh)
            thumbnailed_bytes = fh.getvalue()
        with pyvips.Image.new_from_buffer(thumbnailed_bytes, "") as thumbnailed_image:
            self.assertEqual(thumbnailed_image.get("vips-loader"), "jpegload_buffer")
            self.assertEqual(thumbnailed_image.width, 100)
            self.assertEqual(thumbnailed_image.height, 600)
            self.assertEqual(thumbnailed_image.get("orientation"), 8)  # 270 CCW rotation

        # The generated thumbnail should be wide, not tall, with the default orientation
        self.assertEqual(str(generated_thumbnail), "100x75.webp")
        self.assertEqual(generated_thumbnail.width, 100)
        self.assertEqual(generated_thumbnail.height, 17)

        with BytesIO() as fh:
            save_attachment_contents(f"thumbnail/{path_id}/100x75.webp", fh)
            thumbnailed_bytes = fh.getvalue()
        with pyvips.Image.new_from_buffer(thumbnailed_bytes, "") as thumbnailed_image:
            self.assertEqual(thumbnailed_image.get("vips-loader"), "webpload_buffer")
            self.assertEqual(thumbnailed_image.width, 100)
            self.assertEqual(thumbnailed_image.height, 17)
            self.assertEqual(thumbnailed_image.get("orientation"), 1)

    def test_big_upload(self) -> None:
        # We decline to treat as an image a large single-frame image
        self.login_user(self.example_user("hamlet"))

        with get_test_image_file("img.gif") as image_file:
            with patch.object(pyvips.Image, "new_from_buffer") as mock_from_buffer:
                mock_from_buffer.return_value.width = 1000000
                mock_from_buffer.return_value.height = 1000000
                mock_from_buffer.return_value.get_n_pages.return_value = 1
                response = self.assert_json_success(
                    self.client_post("/json/user_uploads", {"file": image_file})
                )
            path_id = re.sub(r"/user_uploads/", "", response["url"])
            self.assertTrue(Attachment.objects.filter(path_id=path_id).exists())
            self.assertFalse(ImageAttachment.objects.filter(path_id=path_id).exists())

    def test_big_animated_upload(self) -> None:
        # We support uploads of very large frame-count animations --
        # we just do not include all of their frames in the thumbnail
        # preview
        self.login_user(self.example_user("hamlet"))
        with (
            get_test_image_file("img.gif") as image_file,
            patch.object(pyvips.Image, "new_from_buffer") as mock_from_buffer,
            patch("zerver.lib.thumbnail.IMAGE_MAX_ANIMATED_PIXELS", 100000),
        ):
            # A 1000x1000 image has too many pixels to show three frames, so we don't include it
            mock_from_buffer.return_value.width = 1000
            mock_from_buffer.return_value.height = 1000
            mock_from_buffer.return_value.get_n_pages.return_value = 1000000
            response = self.assert_json_success(
                self.client_post("/json/user_uploads", {"file": image_file})
            )
            path_id = re.sub(r"/user_uploads/", "", response["url"])
            self.assertTrue(Attachment.objects.filter(path_id=path_id).exists())
            self.assertFalse(ImageAttachment.objects.filter(path_id=path_id).exists())

            # A 100x100 image, we'll thumbnail the first few frames of.
            mock_from_buffer.return_value.width = 100
            mock_from_buffer.return_value.height = 100
            mock_from_buffer.return_value.get_n_pages.return_value = 1000000
            response = self.assert_json_success(
                self.client_post("/json/user_uploads", {"file": image_file})
            )
            path_id = re.sub(r"/user_uploads/", "", response["url"])
            self.assertTrue(Attachment.objects.filter(path_id=path_id).exists())
            self.assertTrue(ImageAttachment.objects.filter(path_id=path_id).exists())

    def test_bad_upload(self) -> None:
        assert settings.LOCAL_FILES_DIR
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)

        with self.captureOnCommitCallbacks(execute=True):
            with get_test_image_file("truncated.gif") as image_file:
                response = self.assert_json_success(
                    self.client_post("/json/user_uploads", {"file": image_file})
                )
            path_id = re.sub(r"/user_uploads/", "", response["url"])
            self.assertEqual(Attachment.objects.filter(path_id=path_id).count(), 1)

            # This doesn't generate an ImageAttachment row because it's corrupted
            self.assertEqual(ImageAttachment.objects.filter(path_id=path_id).count(), 0)

        # Fake making one, based on if just part of the file is readable
        image_attachment = ImageAttachment.objects.create(
            realm_id=hamlet.realm_id,
            path_id=path_id,
            original_height_px=128,
            original_width_px=128,
            frames=1,
            thumbnail_metadata=[],
            content_type="image/gif",
        )
        with self.thumbnail_formats(ThumbnailFormat("webp", 100, 75, animated=False)):
            self.assert_length(missing_thumbnails(image_attachment), 1)

            with self.assertLogs("zerver.worker.thumbnail", level="ERROR") as error_log:
                self.assertEqual(ensure_thumbnails(image_attachment).generated_thumbnail_count, 0)

        libvips_version = (pyvips.version(0), pyvips.version(1))
        # This error message changed
        if libvips_version < (8, 13):  # nocoverage # branch varies with version
            expected_message = "gifload_buffer: Insufficient data to do anything"
        else:  # nocoverage # branch varies with version
            expected_message = "gifload_buffer: no frames in GIF"
        self.assertTrue(expected_message in error_log.output[0])

        # It should have now been removed
        self.assertEqual(ImageAttachment.objects.filter(path_id=path_id).count(), 0)

    def test_missing_thumbnails(self) -> None:
        image_attachment = ImageAttachment(
            path_id="example",
            original_width_px=150,
            original_height_px=100,
            frames=1,
            thumbnail_metadata=[],
            content_type="image/png",
        )
        with self.thumbnail_formats():
            self.assertEqual(missing_thumbnails(image_attachment), [])

        still_webp = ThumbnailFormat("webp", 100, 75, animated=False, opts="Q=90")
        with self.thumbnail_formats(still_webp):
            self.assertEqual(missing_thumbnails(image_attachment), [still_webp])

        anim_webp = ThumbnailFormat("webp", 100, 75, animated=True, opts="Q=90")
        with self.thumbnail_formats(still_webp, anim_webp):
            # It's not animated, so the animated format doesn't appear at all
            self.assertEqual(missing_thumbnails(image_attachment), [still_webp])

        still_jpeg = ThumbnailFormat("jpeg", 100, 75, animated=False, opts="Q=90")
        with self.thumbnail_formats(still_webp, anim_webp, still_jpeg):
            # But other still formats do
            self.assertEqual(missing_thumbnails(image_attachment), [still_webp, still_jpeg])

        # If we have a rendered 150x100.webp, then we're not missing it
        rendered_still_webp = StoredThumbnailFormat(
            "webp",
            100,
            75,
            animated=False,
            width=150,
            height=50,
            content_type="image/webp",
            byte_size=1234,
        )
        image_attachment.thumbnail_metadata = [asdict(rendered_still_webp)]
        with self.thumbnail_formats(still_webp, anim_webp, still_jpeg):
            self.assertEqual(missing_thumbnails(image_attachment), [still_jpeg])

        # If we have the still, and it's animated, we do still need the animated
        image_attachment.frames = 10
        with self.thumbnail_formats(still_webp, anim_webp, still_jpeg):
            self.assertEqual(missing_thumbnails(image_attachment), [anim_webp, still_jpeg])

    def test_worker_plan_parity(self) -> None:
        still = ThumbnailFormat("webp", 100, 75, False, opts="Q=90")
        animated = ThumbnailFormat("webp", 100, 75, True, opts="Q=75")
        other = ThumbnailFormat("jpeg", 150, 90, False, opts="Q=80")
        stored_still = asdict(
            StoredThumbnailFormat("webp", 100, 75, False, "image/webp", 65, 75, 100)
        )
        stored_portrait = asdict(
            StoredThumbnailFormat("webp", 3024, 4032, False, "image/webp", 100, 200, 400)
        )
        image = ImageAttachment(
            path_id="synthetic/worker",
            content_type="image/tiff",
            original_width_px=100,
            original_height_px=200,
            frames=2,
            thumbnail_metadata=[stored_still, stored_portrait],
        )

        with self.thumbnail_formats(still, animated, other):
            with patch.object(native_thumbnail_status, "_plan_backend", None):
                baseline = [asdict(fmt) for fmt in missing_thumbnails(image)]
            self.assertEqual(baseline, [asdict(animated), asdict(other)])
            with patch.object(native_thumbnail_status, "_plan_backend", SimpleNamespace()):
                self.assertEqual([asdict(fmt) for fmt in plan_missing_thumbnails(image)], baseline)

            try:
                backend = import_module("_zulip_backend")
            except ModuleNotFoundError as error:
                if error.name != "_zulip_backend":
                    raise
                return
            native = getattr(backend, "thumbnail_missing_indexes", None)
            if not callable(native):
                return

            observed: list[list[int] | None] = []

            def witness(*args: object) -> list[int] | None:
                result: list[int] | None = native(*args)
                observed.append(result)
                return result

            with patch.object(
                native_thumbnail_status,
                "_plan_backend",
                SimpleNamespace(thumbnail_missing_indexes=witness),
            ):
                self.assertEqual([asdict(fmt) for fmt in plan_missing_thumbnails(image)], baseline)
                image.thumbnail_metadata = [stored_still, {**stored_portrait, "height": 200.5}]
                self.assertEqual([asdict(fmt) for fmt in plan_missing_thumbnails(image)], baseline)

                image.thumbnail_metadata = [stored_still, stored_portrait]
                image.frames = 1
                with (
                    self.thumbnail_formats(still, animated),
                    patch(
                        "zerver.worker.thumbnail._select_needed_thumbnails", plan_missing_thumbnails
                    ),
                    patch("zerver.worker.thumbnail.save_attachment_contents") as fetch,
                ):
                    self.assertEqual(ensure_thumbnails(image).generated_thumbnail_count, 0)
                    fetch.assert_not_called()

            self.assertEqual(observed, [[1, 2], None, []])

    def test_worker_resume_generates_only_missing_output(self) -> None:
        self.login("hamlet")
        first = ThumbnailFormat("webp", 40, 40, False, opts="Q=85")
        missing = ThumbnailFormat("webp", 80, 80, False, opts="Q=75")
        last = ThumbnailFormat("webp", 120, 120, False, opts="Q=60")
        with self.thumbnail_formats(first, missing, last):
            path = self.upload_and_thumbnail_image("img.png")
            image = ImageAttachment.objects.get(path_id=path)
            self.assert_length(image.thumbnail_metadata, 3)
            all_metadata = image.thumbnail_metadata.copy()
            retained = [all_metadata[0], all_metadata[2]]
            image.thumbnail_metadata = retained.copy()
            image.save(update_fields=["thumbnail_metadata"])
            with (
                patch.object(
                    native_thumbnail_status,
                    "missing_indexes",
                    wraps=native_thumbnail_status.missing_indexes,
                ) as plan,
                patch(
                    "zerver.worker.thumbnail.store_message_attachment",
                    wraps=store_message_attachment,
                ) as store,
            ):
                self.assertEqual(ensure_thumbnails(image).generated_thumbnail_count, 1)
                if native_thumbnail_status.plan_available():
                    plan.assert_called_once()
                else:
                    plan.assert_not_called()
                store.assert_called_once()
                self.assertEqual(store.call_args.args[0], get_image_thumbnail_path(image, missing))
                self.assertEqual(ensure_thumbnails(image).generated_thumbnail_count, 0)
                store.assert_called_once()
            image.refresh_from_db()
            self.assertEqual(image.thumbnail_metadata, [*retained, all_metadata[1]])

    def test_transcoded_format(self) -> None:
        image_attachment = ImageAttachment(
            path_id="example",
            original_width_px=150,
            original_height_px=100,
            frames=1,
            thumbnail_metadata=[],
            content_type="image/tiff",
        )
        still_webp = ThumbnailFormat("webp", 100, 75, animated=False, opts="Q=90")
        with self.thumbnail_formats(still_webp):
            # We add a high-resolution transcoded format if the image isn't in INLINE_MIME_TYPES:
            transcoded = ThumbnailFormat("webp", 4032, 3024, animated=False)
            self.assertEqual(missing_thumbnails(image_attachment), [still_webp, transcoded])

            # We flip to being portrait if the image is higher than it is wide
            transcoded = ThumbnailFormat("webp", 3024, 4032, animated=False)
            image_attachment.original_height_px = 300
            self.assertEqual(missing_thumbnails(image_attachment), [still_webp, transcoded])

            # The format is not animated, even if the original was
            image_attachment.original_height_px = 100
            image_attachment.frames = 10
            transcoded = ThumbnailFormat("webp", 4032, 3024, animated=False)
            self.assertEqual(missing_thumbnails(image_attachment), [still_webp, transcoded])

            # We do not store on the image_attachment if we generated
            # a transcoded version; it just picks the largest format
            # if one is called for.
            self.assertEqual(get_transcoded_format(image_attachment), None)
            image_attachment.thumbnail_metadata = [
                asdict(
                    StoredThumbnailFormat(
                        "webp",
                        100,
                        75,
                        animated=False,
                        content_type="image/webp",
                        width=100,
                        height=75,
                        byte_size=100,
                    )
                ),
                asdict(
                    StoredThumbnailFormat(
                        "webp",
                        840,
                        560,
                        animated=False,
                        content_type="image/webp",
                        width=747,
                        height=560,
                        byte_size=800,
                    )
                ),
                asdict(
                    StoredThumbnailFormat(
                        "webp",
                        4032,
                        3024,
                        animated=False,
                        content_type="image/webp",
                        width=4032,
                        height=3024,
                        byte_size=2000,
                    )
                ),
            ]
            self.assertEqual(
                get_transcoded_format(image_attachment),
                ThumbnailFormat("webp", 4032, 3024, animated=False),
            )
            image_attachment.content_type = "image/png"
            self.assertEqual(get_transcoded_format(image_attachment), None)
            # A null or empty content-type is judged inline.
            image_attachment.content_type = None
            self.assertEqual(get_transcoded_format(image_attachment), None)
            image_attachment.content_type = ""
            self.assertEqual(get_transcoded_format(image_attachment), None)

    def test_transcoded_manifest_parity(self) -> None:
        realm = self.example_user("hamlet").realm
        small = asdict(StoredThumbnailFormat("webp", 840, 560, False, "image/webp", 5, 5, 20))
        first = asdict(StoredThumbnailFormat("webp", 4032, 3024, False, "image/webp", 60, 40, 100))
        last = asdict(StoredThumbnailFormat("webp", 3024, 4032, False, "image/webp", 40, 60, 200))

        def image(name: str, content_type: str, metadata: object) -> ImageAttachment:
            return ImageAttachment.objects.create(
                realm=realm,
                path_id=f"1/native-transcode/{name}",
                content_type=content_type,
                original_width_px=60,
                original_height_px=40,
                frames=1,
                thumbnail_metadata=metadata,
            )

        tie = image("tie", "image/tiff", [small, first, last])
        largest_first = image("largest-first", "image/tiff", [first, small])
        inline = image("inline", "image/png", [last])
        pending = image("pending", "image/tiff", [])
        paths = [pending.path_id, tie.path_id, inline.path_id, largest_first.path_id]

        def manifest() -> AttachmentData:
            with patch("zerver.lib.thumbnail.queue_event_on_commit") as queued:
                data = manifest_and_get_user_upload_previews(realm.id, "", path_ids=paths)
            queued.assert_called_once_with(
                "thumbnail", {"id": pending.id, "path_id": pending.path_id}
            )
            return data

        with patch.object(native_thumbnail_status, "_selection_backend", None):
            expected = manifest()
        self.assertEqual(
            list(expected.image_metadata),
            [tie.path_id, largest_first.path_id, inline.path_id, pending.path_id],
        )
        self.assertEqual(asdict(expected.image_metadata[tie.path_id])["transcoded_image"], last)
        self.assertEqual(
            asdict(expected.image_metadata[largest_first.path_id])["transcoded_image"], first
        )
        self.assertIsNone(expected.image_metadata[inline.path_id].transcoded_image)
        self.assertIsNone(expected.image_metadata[pending.path_id].url)
        with patch.object(native_thumbnail_status, "_selection_backend", SimpleNamespace()):
            self.assertEqual(asdict(manifest()), asdict(expected))

        try:
            backend = import_module("_zulip_backend")
        except ModuleNotFoundError as error:
            if error.name != "_zulip_backend":
                raise
            return
        native = getattr(backend, "thumbnail_transcode_indexes", None)
        if not callable(native):
            return
        accepted: list[list[int] | None] = []

        def witness(rows: list[object]) -> list[int] | None:
            self.assert_length(rows, 2)
            result: list[int] | None = native(rows)
            accepted.append(result)
            return result

        with patch.object(
            native_thumbnail_status,
            "_selection_backend",
            SimpleNamespace(thumbnail_transcode_indexes=witness),
        ):
            self.assertEqual(asdict(manifest()), asdict(expected))
            # Python handles old non-integer dimensions. A decline in a
            # second image must leave the first selection equally unchanged.
            largest_first.thumbnail_metadata = [{**first, "width": 60.5}, small]
            largest_first.save(update_fields=["thumbnail_metadata"])
            with patch.object(native_thumbnail_status, "_selection_backend", None):
                fallback = manifest()
            self.assertEqual(asdict(manifest()), asdict(fallback))
            # A legacy empty object is different from a pending empty list.
            largest_first.thumbnail_metadata = {}
            largest_first.save(update_fields=["thumbnail_metadata"])
            with patch.object(native_thumbnail_status, "_selection_backend", None):
                fallback = manifest()
            self.assertEqual(asdict(manifest()), asdict(fallback))
        self.assertEqual(accepted, [[2, 0], None, None])

    def test_missing_thumbnails_missing_content_type(self) -> None:
        # A null or empty content-type (some old uploads) must not
        # crash; it is judged inline, like get_transcoded_format.
        image_attachment = ImageAttachment(
            path_id="example",
            original_width_px=150,
            original_height_px=100,
            frames=1,
            thumbnail_metadata=[],
            content_type="image/tiff",
        )
        still_webp = ThumbnailFormat("webp", 100, 75, animated=False, opts="Q=90")
        transcoded = ThumbnailFormat("webp", 4032, 3024, animated=False)
        with self.thumbnail_formats(still_webp):
            # A known non-inline type gets the extra transcoded format.
            self.assertEqual(missing_thumbnails(image_attachment), [still_webp, transcoded])

            # A missing content-type does not, and does not crash.
            for missing_content_type in (None, ""):
                image_attachment.content_type = missing_content_type
                self.assertEqual(missing_thumbnails(image_attachment), [still_webp])

    def test_maybe_thumbnail_from_stream(self) -> None:
        # If we put the file in place directly (e.g. simulating a
        # chunked upload), and then use the streaming source to
        # create the attachment, we still thumbnail correctly.
        hamlet = self.example_user("hamlet")
        path_id = generate_message_upload_path(str(hamlet.realm.id), "img.png")
        store_message_attachment(
            path_id, "img.png", "image/png", read_test_image_file("img.png"), hamlet, hamlet.realm
        )
        source = attachment_source(path_id)
        create_attachment("img.png", path_id, "image/png", source, hamlet, hamlet.realm)
        self.assertTrue(ImageAttachment.objects.filter(path_id=path_id).exists())


class TestThumbnailRetrieval(ZulipTestCase):
    def test_get_thumbnail(self) -> None:
        assert settings.LOCAL_FILES_DIR
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)

        webp_anim = ThumbnailFormat("webp", 100, 75, animated=True)
        webp_still = ThumbnailFormat("webp", 100, 75, animated=False)
        with self.thumbnail_formats(webp_anim, webp_still):
            with (
                self.captureOnCommitCallbacks(execute=True),
                get_test_image_file("animated_unequal_img.gif") as image_file,
            ):
                json_response = self.assert_json_success(
                    self.client_post("/json/user_uploads", {"file": image_file})
                )
                path_id = re.sub(r"/user_uploads/", "", json_response["url"])

                # Image itself is available immediately
                response = self.client_get(f"/user_uploads/{path_id}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Content-Type"], "image/gif")
                consume_response(response)

                # Format we don't have
                response = self.client_get(f"/user_uploads/thumbnail/{path_id}/1x1.png")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers["Content-Type"], "image/png")
                consume_response(response)

                # Exit the block, triggering the thumbnailing worker

            thumbnail_response = self.client_get(
                f"/user_uploads/thumbnail/{path_id}/{webp_still!s}"
            )
            self.assertEqual(thumbnail_response.status_code, 200)
            self.assertEqual(thumbnail_response.headers["Content-Type"], "image/webp")
            self.assertLess(
                int(thumbnail_response.headers["Content-Length"]),
                int(response.headers["Content-Length"]),
            )
            consume_response(thumbnail_response)

            animated_response = self.client_get(f"/user_uploads/thumbnail/{path_id}/{webp_anim!s}")
            self.assertEqual(animated_response.status_code, 200)
            self.assertEqual(animated_response.headers["Content-Type"], "image/webp")
            self.assertLess(
                int(thumbnail_response.headers["Content-Length"]),
                int(animated_response.headers["Content-Length"]),
            )
            consume_response(animated_response)

            # Invalid thumbnail format
            response = self.client_get(f"/user_uploads/thumbnail/{path_id}/bogus")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.headers["Content-Type"], "image/png")
            consume_response(response)

            # path_id for a non-image
            with (
                self.captureOnCommitCallbacks(execute=True),
                get_test_image_file("text.txt") as text_file,
            ):
                json_response = self.assert_json_success(
                    self.client_post("/json/user_uploads", {"file": text_file})
                )
                text_path_id = re.sub(r"/user_uploads/", "", json_response["url"])
            response = self.client_get(f"/user_uploads/thumbnail/{text_path_id}/{webp_still!s}")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.headers["Content-Type"], "image/png")
            consume_response(response)

        # Shrink the list of formats, and check that we can still get
        # the thumbnails that were generated at the time
        with self.thumbnail_formats(webp_still):
            response = self.client_get(f"/user_uploads/thumbnail/{path_id}/{webp_still!s}")
            self.assertEqual(response.status_code, 200)
            consume_response(response)

            response = self.client_get(f"/user_uploads/thumbnail/{path_id}/{webp_anim!s}")
            self.assertEqual(response.status_code, 200)
            consume_response(response)

        # Grow the format list, and check that fetching that new
        # format generates all of the missing formats
        jpeg_still = ThumbnailFormat("jpg", 100, 75, animated=False)
        big_jpeg_still = ThumbnailFormat("jpg", 200, 150, animated=False)
        with (
            self.thumbnail_formats(webp_still, jpeg_still, big_jpeg_still),
            patch.object(
                pyvips.Image, "thumbnail_buffer", wraps=pyvips.Image.thumbnail_buffer
            ) as thumb_mock,
        ):
            small_response = self.client_get(f"/user_uploads/thumbnail/{path_id}/{jpeg_still!s}")
            self.assertEqual(small_response.status_code, 200)
            self.assertEqual(small_response.headers["Content-Type"], "image/jpeg")
            consume_response(small_response)
            # This made two thumbnails
            self.assertEqual(thumb_mock.call_count, 2)

            thumb_mock.reset_mock()
            big_response = self.client_get(f"/user_uploads/thumbnail/{path_id}/{big_jpeg_still!s}")
            self.assertEqual(big_response.status_code, 200)
            self.assertEqual(big_response.headers["Content-Type"], "image/jpeg")
            consume_response(big_response)
            thumb_mock.assert_not_called()

            self.assertLess(
                int(small_response.headers["Content-Length"]),
                int(big_response.headers["Content-Length"]),
            )

        # Upload a static image, and verify that we only generate the still versions
        with self.thumbnail_formats(webp_anim, webp_still, jpeg_still):
            with (
                self.captureOnCommitCallbacks(execute=True),
                get_test_image_file("img.tif") as image_file,
            ):
                json_response = self.assert_json_success(
                    self.client_post("/json/user_uploads", {"file": image_file})
                )
                path_id = re.sub(r"/user_uploads/", "", json_response["url"])
                # Exit the block, triggering the thumbnailing worker

            still_response = self.client_get(f"/user_uploads/thumbnail/{path_id}/{webp_still!s}")
            self.assertEqual(still_response.status_code, 200)
            self.assertEqual(still_response.headers["Content-Type"], "image/webp")
            consume_response(still_response)

            # We can request -anim -- we didn't render it, but we the
            # "closest we rendered" logic kicks in, and we get the
            # still webp, rather than a 404
            animated_response = self.client_get(f"/user_uploads/thumbnail/{path_id}/{webp_anim!s}")
            self.assertEqual(animated_response.status_code, 200)
            self.assertEqual(animated_response.headers["Content-Type"], "image/webp")
            consume_response(animated_response)
            # Double-check that we don't actually have the animated version, by comparing file sizes
            self.assertEqual(
                animated_response.headers["Content-Length"],
                still_response.headers["Content-Length"],
            )

            response = self.client_get(f"/user_uploads/thumbnail/{path_id}/{jpeg_still!s}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Content-Type"], "image/jpeg")
            consume_response(response)

    def test_native_stored_variant_probe(self) -> None:
        requested = BaseThumbnailFormat("webp", 100, 75, False)
        matching = asdict(StoredThumbnailFormat("webp", 100, 75, False, "image/webp", 65, 75, 7))
        other = asdict(StoredThumbnailFormat("webp", 100, 75, True, "image/webp", 65, 75, 9))
        rows = [matching, other]
        with (
            patch.object(native_thumbnail_status, "_stored_variant_backend", None),
        ):
            self.assertIsNone(stored_thumbnail_variant(rows, requested))
        with patch.object(native_thumbnail_status, "_stored_variant_backend", SimpleNamespace()):
            self.assertIsNone(stored_thumbnail_variant(rows, requested))

        try:
            backend = import_module("_zulip_backend")
        except ModuleNotFoundError as error:
            if error.name != "_zulip_backend":
                raise
            return
        native = getattr(backend, "thumbnail_has_stored_variant", None)
        if not callable(native):
            return
        with patch.object(native_thumbnail_status, "_stored_variant_backend", backend):
            self.assertIsNone(stored_thumbnail_variant([matching], requested))
            for desired in (
                requested,
                BaseThumbnailFormat("webp", 100, 75, True),
                BaseThumbnailFormat("webp", 100, 76, False),
            ):
                self.assertEqual(
                    stored_thumbnail_variant(rows, desired),
                    desired in [StoredThumbnailFormat(**row) for row in rows],
                )
            # A match cannot short-circuit validation of a later row.
            self.assertIsNone(
                stored_thumbnail_variant([matching, {**other, "height": 75.5}], requested)
            )

    def test_native_stored_variant_retrieval(self) -> None:
        self.login("hamlet")
        small = ThumbnailFormat("webp", 40, 40, False)
        large = ThumbnailFormat("webp", 100, 100, False)
        with self.thumbnail_formats(small, large):
            path_id = self.upload_and_thumbnail_image("img.png")
        url = f"/user_uploads/thumbnail/{path_id}/{large!s}"

        def fetch(path: str) -> tuple[bytes, str]:
            reply = self.client_get(path)
            self.assertEqual(reply.status_code, 200)
            assert reply.streaming
            return b"".join(cast(StreamingHttpResponse, reply)), reply["Content-Type"]

        with patch.object(native_thumbnail_status, "_stored_variant_backend", None):
            baseline = fetch(url)

        results: list[bool | None] = []
        native_probe = native_thumbnail_status.has_stored_variant

        def observe(metadata: object, requested: BaseThumbnailFormat) -> bool | None:
            result = native_probe(metadata, requested)
            results.append(result)
            return result

        with (
            self.thumbnail_formats(small),
            patch.object(native_thumbnail_status, "has_stored_variant", side_effect=observe),
            patch("zerver.views.upload.ensure_thumbnails") as generate,
            patch(
                "zerver.views.upload.closest_thumbnail_format",
                wraps=closest_thumbnail_format,
            ) as closest,
        ):
            # This variant is no longer configured, but the stored file
            # must still be served byte-for-byte without a new generation.
            self.assertEqual(fetch(url), baseline)
            closest.assert_not_called()

            # Django's usual closest-selection path still handles an old
            # nonconfigured size that was never generated.
            self.assertEqual(fetch(f"/user_uploads/thumbnail/{path_id}/99x99.webp"), baseline)
            closest.assert_called_once()
            generate.assert_not_called()

            # Unsupported legacy metadata still takes the complete Python
            # path, which accepts non-integer physical height.
            image = ImageAttachment.objects.get(path_id=path_id)
            image.thumbnail_metadata[0]["height"] = 30.5
            image.save(update_fields=["thumbnail_metadata"])
            self.assertEqual(fetch(url), baseline)
            closest.assert_called_once()
            generate.assert_not_called()

        expected = (
            [True, False, None]
            if native_thumbnail_status.stored_variant_available()
            else [None, None, None]
        )
        self.assertEqual(results, expected)

    def test_closest_format(self) -> None:
        self.login_user(self.example_user("hamlet"))

        webp_anim = ThumbnailFormat("webp", 100, 75, animated=True)
        webp_still = ThumbnailFormat("webp", 100, 75, animated=False)
        tiny_webp_still = ThumbnailFormat("webp", 10, 10, animated=False)
        gif_still = ThumbnailFormat("gif", 100, 75, animated=False)
        with (
            self.thumbnail_formats(webp_anim, webp_still, tiny_webp_still, gif_still),
            self.captureOnCommitCallbacks(execute=True),
            get_test_image_file("animated_img.gif") as image_file,
        ):
            json_response = self.assert_json_success(
                self.client_post("/json/user_uploads", {"file": image_file})
            )
            path_id = re.sub(r"/user_uploads/", "", json_response["url"])
            # Exit the block, triggering the thumbnailing worker

        image_attachment = ImageAttachment.objects.get(path_id=path_id)
        rendered_formats = [
            StoredThumbnailFormat(**data) for data in image_attachment.thumbnail_metadata
        ]
        request = HttpRequest()
        request.META["HTTP_ACCEPT"] = "image/webp, image/*, */*;q=0.8"

        # Prefer to match -animated, even though we have a .gif
        self.assertEqual(
            str(
                closest_thumbnail_format(
                    ThumbnailFormat("gif", 100, 75, animated=True), request, rendered_formats
                )
            ),
            "100x75-anim.webp",
        )

        # Match the extension, even if we're an exact match for a different size
        self.assertEqual(
            str(
                closest_thumbnail_format(
                    ThumbnailFormat("gif", 10, 10, animated=False), request, rendered_formats
                )
            ),
            "100x75.gif",
        )

        # If they request an extension we don't do, then we look for the formats they prefer
        self.assertEqual(
            str(
                closest_thumbnail_format(
                    ThumbnailFormat("tif", 10, 10, animated=False), request, rendered_formats
                )
            ),
            "10x10.webp",
        )
        request = HttpRequest()
        request.META["HTTP_ACCEPT"] = "image/webp;q=0.9, image/gif"
        self.assertEqual(
            str(
                closest_thumbnail_format(
                    ThumbnailFormat("tif", 10, 10, animated=False), request, rendered_formats
                )
            ),
            "100x75.gif",
        )
        request = HttpRequest()
        request.META["HTTP_ACCEPT"] = "image/gif"
        self.assertEqual(
            str(
                closest_thumbnail_format(
                    ThumbnailFormat("tif", 10, 10, animated=False), request, rendered_formats
                )
            ),
            "100x75.gif",
        )

        # Closest width
        request = HttpRequest()
        request.META["HTTP_ACCEPT"] = "image/webp, image/*, */*;q=0.8"
        self.assertEqual(
            str(
                closest_thumbnail_format(
                    ThumbnailFormat("webp", 20, 100, animated=False), request, rendered_formats
                )
            ),
            "10x10.webp",
        )
        self.assertEqual(
            str(
                closest_thumbnail_format(
                    ThumbnailFormat("webp", 80, 10, animated=False), request, rendered_formats
                )
            ),
            "100x75.webp",
        )

        # Smallest filesize if they have no media preference
        request = HttpRequest()
        request.META["HTTP_ACCEPT"] = "image/gif, image/webp"
        self.assertEqual(
            str(
                closest_thumbnail_format(
                    ThumbnailFormat("tif", 100, 75, animated=False), request, rendered_formats
                )
            ),
            "100x75.webp",
        )


class ThumbnailStatusEndpointTest(ZulipTestCase):
    def test_thumbnail_status_with_ready_thumbnails(self) -> None:
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)

        with self.thumbnail_formats(ThumbnailFormat("webp", 100, 75, animated=True)):
            with self.captureOnCommitCallbacks(execute=True):
                with get_test_image_file("animated_unequal_img.gif") as image_file:
                    json_response = self.assert_json_success(
                        self.client_post("/json/user_uploads", {"file": image_file})
                    )
                path_id = re.sub(r"/user_uploads/", "", json_response["url"])

            result = self.client_get(f"/json/thumbnail/status/{path_id}")
            self.assert_json_success(result)
            self.assertEqual(result.json()["has_thumbnail"], True)

    def test_thumbnail_status_without_ready_thumbnails(self) -> None:
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)

        with self.thumbnail_formats(ThumbnailFormat("webp", 100, 75, animated=True)):
            # Don't execute callbacks, so thumbnails aren't generated
            with get_test_image_file("animated_unequal_img.gif") as image_file:
                json_response = self.assert_json_success(
                    self.client_post("/json/user_uploads", {"file": image_file})
                )
            path_id = re.sub(r"/user_uploads/", "", json_response["url"])

        result = self.client_get(f"/json/thumbnail/status/{path_id}")
        self.assert_json_success(result)
        self.assertEqual(result.json()["has_thumbnail"], False)

    def test_thumbnail_status_for_non_image_file(self) -> None:
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)

        fp = StringIO("zulip text file!")
        fp.name = "zulip.txt"

        json_response = self.assert_json_success(
            self.client_post("/json/user_uploads", {"file": fp})
        )
        path_id = re.sub(r"/user_uploads/", "", json_response["url"])

        result = self.client_get(f"/json/thumbnail/status/{path_id}")
        self.assert_json_error(result, "Invalid attachment")

    def test_thumbnail_status_nonexistent_file(self) -> None:
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)

        result = self.client_get("/json/thumbnail/status/2/nonexistent/fake.gif")
        self.assert_json_error(result, "Invalid attachment")

    def test_thumbnail_status_without_access(self) -> None:
        hamlet = self.example_user("hamlet")
        iago = self.example_user("iago")

        path_id = f"{hamlet.realm_id}/31/4CBjtTLYZhk66pZrF8hnYGwc/img.gif"
        create_attachment("img.gif", path_id, "image/gif", b"gif_content", hamlet, hamlet.realm)

        # Iago is not a recipient of the uploaded file so they
        # should not be able to query its thumbnail status.
        self.login_user(iago)
        result = self.client_get(f"/json/thumbnail/status/{path_id}")
        self.assert_json_error(result, "Invalid attachment")
