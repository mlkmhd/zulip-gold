from types import ModuleType
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils.translation import override

from zerver.lib import emoji
from zerver.lib.emoji import EmojiData, get_emoji_data
from zerver.lib.exceptions import JsonableError


class PreparedEmojiMetadataRustTest(SimpleTestCase):
    def test_one_realm_lookup_and_exact_precedence(self) -> None:
        cases = [
            ("astonished", {}, EmojiData("1f632", "unicode_emoji")),
            ("astonished", {"astonished": {"id": 2**90}}, EmojiData(str(2**90), "realm_emoji")),
            ("zulip", {"zulip": {"id": 18}}, EmojiData("18", "realm_emoji")),
            ("zulip", {}, EmojiData("zulip", "zulip_extra_emoji")),
            ("custom", {"custom": {"id": 53}}, EmojiData("53", "realm_emoji")),
        ]
        for name, active, expected in cases:
            with (
                self.subTest(name=name, active=active),
                patch.object(
                    emoji, "get_name_keyed_dict_for_active_realm_emoji", return_value=active
                ) as load,
            ):
                self.assertEqual(get_emoji_data(19, name), expected)
                load.assert_called_once_with(19)
                load.reset_mock()
                with patch.object(emoji, "_rust_emoji_metadata", ModuleType("old")):
                    self.assertEqual(get_emoji_data(19, name), expected)
                load.assert_called_once_with(19)

    def test_missing_and_noncanonical_provider_preserve_exact_behavior(self) -> None:
        with (
            override("en"),
            patch.object(
                emoji, "get_name_keyed_dict_for_active_realm_emoji", return_value={}
            ) as load,
            self.assertRaisesRegex(
                JsonableError, "^Emoji 'synthetic_missing_name' does not exist$"
            ),
        ):
            get_emoji_data(19, "synthetic_missing_name")
        load.assert_called_once_with(19)

        class CustomLookup:
            def get(self, name: str, default: object = None) -> dict[str, str]:
                return {"id": "custom-id"}

        with patch.object(
            emoji, "get_name_keyed_dict_for_active_realm_emoji", return_value=CustomLookup()
        ):
            self.assertEqual(
                get_emoji_data(19, "astonished"), EmojiData("custom-id", "realm_emoji")
            )

    def test_current_native_receives_only_prepared_catalogs(self) -> None:
        native = emoji._rust_emoji_metadata
        if native is None:
            self.skipTest("requires current native emoji metadata")
        active = {"zulip": {"id": 99}}
        with (
            patch.object(emoji, "get_name_keyed_dict_for_active_realm_emoji", return_value=active),
            patch.object(
                native,
                "select_prepared_emoji_metadata",
                wraps=native.select_prepared_emoji_metadata,
            ) as select,
        ):
            self.assertEqual(get_emoji_data(19, "zulip"), EmojiData("99", "realm_emoji"))
        select.assert_called_once_with("zulip", active, emoji.name_to_codepoint)
