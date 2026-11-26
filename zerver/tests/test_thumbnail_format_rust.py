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

    def test_native_parser_preserves_canonical_and_legacy_paths(self) -> None:
        backend = thumbnail._format_parse_backend
        if backend is None or not hasattr(backend, "parse_canonical_thumbnail_format"):
            self.skipTest("No thumbnail format parse ABI")
        for value in (
            "150x100.webp",
            "0x100-anim.PNG",
            "00x0001.webp",
            "000000000000000000000000001x1.webp",
            "150x100.webp\n",
            "۱۵۰x１００.عکس",
            "1x2._",
            "bad.webp",
            "2x9223372036854775808.webp",
        ):
            with patch.object(thumbnail, "_format_parse_backend", None):
                expected = thumbnail.BaseThumbnailFormat.from_string(value)
            actual = thumbnail.BaseThumbnailFormat.from_string(value)
            self.assertEqual(actual, expected)

        for value in ("150x100.webp\n", "۱۵۰x１００.عکس", "2x9223372036854775808.webp"):
            self.assertIsNone(backend.parse_canonical_thumbnail_format(value))

        with patch.object(
            backend,
            "parse_canonical_thumbnail_format",
            wraps=backend.parse_canonical_thumbnail_format,
        ) as witness:
            actual = thumbnail.ThumbnailFormat.from_string("150x100-anim.webp")
        self.assertIs(type(actual), thumbnail.ThumbnailFormat)
        self.assertEqual(actual, thumbnail.ThumbnailFormat("webp", 150, 100, True))
        witness.assert_called_once_with("150x100-anim.webp")
