import re
from types import ModuleType
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import emoji
from zerver.lib.emoji import translate_emoticons


class PreparedEmoticonTextRustTest(SimpleTestCase):
    def test_real_catalog_order_and_unicode_match_original(self) -> None:
        source = "آرش 😀 " + " ".join(key + key for key in emoji.EMOTICON_CONVERSIONS)
        for text in (source, "", "hello", "\udfff" + source):
            with patch.object(emoji, "_rust_emoticon_text", ModuleType("old")):
                expected = translate_emoticons(text)
            self.assertEqual(translate_emoticons(text), expected)

    def test_altered_catalog_replays_python_behavior(self) -> None:
        key = next(iter(emoji.EMOTICON_CONVERSIONS))
        with patch.dict(emoji.EMOTICON_CONVERSIONS, {key: ":custom:"}):
            with patch.object(emoji, "_rust_emoticon_text", None):
                expected = translate_emoticons(key)
            self.assertEqual(translate_emoticons(key), expected)
        with patch.object(re, "sub", return_value="mocked") as sub:
            self.assertEqual(translate_emoticons(key), "mocked")
        self.assertEqual(sub.call_count, len(emoji.EMOTICON_CONVERSIONS))

    def test_current_extension_checks_the_actual_ordered_catalog(self) -> None:
        native = emoji._rust_emoticon_text
        if native is None:
            self.skipTest("requires current exact prepared emoticon formatter")
        text = next(iter(emoji.EMOTICON_CONVERSIONS))
        with patch.object(
            native, "translate_prepared_emoticons", wraps=native.translate_prepared_emoticons
        ) as normalize:
            self.assertEqual(translate_emoticons(text), emoji.EMOTICON_CONVERSIONS[text])
        normalize.assert_called_once_with(text, emoji.EMOTICON_CONVERSIONS)
        self.assertIsNone(
            native.translate_prepared_emoticons(
                text, dict(reversed(emoji.EMOTICON_CONVERSIONS.items()))
            )
        )
