from unittest import TestCase
from unittest.mock import patch

from zerver.lib import emoji
from zerver.lib.exceptions import JsonableError


class CustomEmojiNameRustTest(TestCase):
    def test_actual_validation_preserves_all_error_kinds(self) -> None:
        for good in ("a", "8", "word_with-dash", "a\n"):
            emoji.check_valid_emoji_name(good)
        for value, fragment in (
            ("", "name is missing"),
            ("a_\n", "must end"),
            ("a b", "must contain only lowercase"),
            ("\n", "must contain only lowercase"),
            ("a\n\n", "must contain only lowercase"),
            ("x\ud800", "must contain only lowercase"),
        ):
            with self.subTest(value=value), self.assertRaisesRegex(JsonableError, fragment):
                emoji.check_valid_emoji_name(value)

    def test_subclass_truth_value_retains_python_and_error_stays_translated(self) -> None:
        class FalseName(str):
            __slots__ = ()

            def __bool__(self) -> bool:
                return False

        with (
            patch.object(emoji, "_", side_effect=lambda text: f"tr: {text}"),
            self.assertRaisesRegex(JsonableError, "^tr: Emoji name is missing$"),
        ):
            emoji.check_valid_emoji_name(FalseName("looks-valid"))

    def test_uses_current_extension_on_exact_string(self) -> None:
        classify = emoji._classify_prepared_custom_emoji_name
        if classify is None:
            self.skipTest("requires the current native custom emoji name classifier")
        with patch.object(emoji, "_classify_prepared_custom_emoji_name", wraps=classify) as native:
            emoji.check_valid_emoji_name("valid-1")
        native.assert_called_once_with("valid-1")
