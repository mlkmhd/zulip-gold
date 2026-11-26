from itertools import product
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import thumbnail


class ThumbnailFormatRustTest(SimpleTestCase):
    def test_native_format_string_matches_existing_object_format(self) -> None:
        backend = thumbnail._format_string_backend
        if backend is None or not hasattr(backend, "thumbnail_format_string"):
            self.skipTest("No thumbnail format string ABI")
        for width, height, animated, extension in product(
            (0, 150, -1, 1 << 70), (5, 100), (False, True), ("webp", "PNG", "عکس", chr(0xD800))
        ):
            item = thumbnail.BaseThumbnailFormat(extension, width, height, animated)
            with patch.object(thumbnail, "_format_string_backend", None):
                expected = str(item)
            self.assertEqual(str(item), expected)
        item = thumbnail.BaseThumbnailFormat("webp", 150, 100, True)
        with patch.object(
            backend, "thumbnail_format_string", wraps=backend.thumbnail_format_string
        ) as witness:
            self.assertEqual(str(item), "150x100-anim.webp")
        witness.assert_called_once_with(150, 100, True, "webp")
