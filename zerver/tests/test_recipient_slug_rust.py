from types import ModuleType
from typing import SupportsIndex
from unittest.mock import patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import url_decoding
from zerver.lib.narrow_helpers import NarrowTerm
from zerver.lib.url_decoding import parse_narrow_url, parse_recipient_slug


class RecipientSlugDjangoRustTest(SimpleTestCase):
    def test_exact_id_order_and_untouched_label(self) -> None:
        self.assertEqual(parse_recipient_slug("12,09,12-سلام-a"), ([12, 9, 12], "سلام-a"))
        self.assertEqual(parse_recipient_slug("1-"), (1, ""))
        self.assertEqual(parse_recipient_slug("0-.2E"), (0, ".2E"))
        self.assertEqual(parse_recipient_slug("9223372036854775807-a"), (2**63 - 1, "a"))
        self.assertEqual(parse_recipient_slug("١٢-کانال"), (12, "کانال"))
        self.assertEqual(parse_recipient_slug("+09, 1_2 -label"), ([9, 12], "label"))
        self.assertEqual(parse_recipient_slug("9223372036854775808-label"), (2**63, "label"))
        self.assertEqual(parse_recipient_slug("1-\udfff"), (1, "\udfff"))
        for invalid in ("legacy-stream", "", "9", "9,,10-a", "9,10,-label", "9.2-a"):
            with self.subTest(slug=invalid):
                self.assertIsNone(parse_recipient_slug(invalid))

    def test_uses_original_string_subclass_split(self) -> None:
        class SplitSlug(str):
            __slots__ = ()

            @override
            def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
                return ["7,2", "custom-label"]

        self.assertEqual(parse_recipient_slug(SplitSlug("not-the-label")), ([7, 2], "custom-label"))

    def test_real_narrow_parser_keeps_channel_dm_and_near_terms(self) -> None:
        channel_link = "#narrow/channel/001-مثال/topic/A.20B/near/8"
        dm_link = "#narrow/dm/8,2,8-group/near/5"
        expected_channel = [
            NarrowTerm("channel", 1, False),
            NarrowTerm("topic", "A B", False),
            NarrowTerm("near", 8, False),
        ]
        expected_dm = [NarrowTerm("dm", [8, 2, 8], False), NarrowTerm("near", 5, False)]
        self.assertEqual(parse_narrow_url(channel_link), expected_channel)
        self.assertEqual(parse_narrow_url(dm_link), expected_dm)
        with patch.object(url_decoding, "_rust_narrow_recipient_slug", ModuleType("old")):
            self.assertEqual(parse_narrow_url(channel_link), expected_channel)
            self.assertEqual(parse_narrow_url(dm_link), expected_dm)

    def test_native_entrypoint_handles_canonical_group(self) -> None:
        native = url_decoding._rust_narrow_recipient_slug
        if native is None:
            self.skipTest("requires the current Rust narrow recipient parser")
        with patch.object(
            native, "parse_narrow_recipient_slug", wraps=native.parse_narrow_recipient_slug
        ) as call:
            self.assertEqual(parse_recipient_slug("12,09,12-سلام-a"), ([12, 9, 12], "سلام-a"))
        call.assert_called_once_with("12,09,12-سلام-a")
