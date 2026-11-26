from contextlib import nullcontext
from unittest.mock import patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import alert_words


class PreparedAlertWordAdditionsRustTest(SimpleTestCase):
    def test_first_position_last_original_spelling_for_normal_inputs(self) -> None:
        existing = ["Keep", "İ"]
        final_tea = "tea".upper()
        incoming = ["KEEP", "tea", "cake", final_tea, "ΑΣ", "ας", "i\u0307", "سلام"]
        backend = alert_words._rust_prepared_alert_word_additions
        witness = (
            patch.object(
                backend,
                "select_prepared_alert_word_additions",
                wraps=backend.select_prepared_alert_word_additions,
            )
            if backend is not None
            else nullcontext()
        )
        with patch.object(alert_words, "_rust_alert_words", None), witness as native:
            with patch.object(alert_words, "_rust_prepared_alert_word_additions", None):
                expected = list(alert_words._select_alert_word_additions(existing, incoming))
            current = list(alert_words._select_alert_word_additions(existing, tuple(incoming)))
            self.assertEqual(current, expected)
            self.assertEqual(current, ["TEA", "cake", "ας", "سلام"])
            self.assertIs(current[0], final_tea)
            if native is not None:
                native.assert_called_once_with(existing, tuple(incoming))

    def test_generator_subtype_and_surrogate_keep_python_behavior(self) -> None:
        class CustomWord(str):
            __slots__ = ()

            @override
            def lower(self) -> str:
                return "keep"

        with patch.object(alert_words, "_rust_alert_words", None):
            self.assertEqual(
                list(alert_words._select_alert_word_additions(["Keep"], [CustomWord("Other")])), []
            )
            special = "ثبت\ud800"
            self.assertEqual(
                list(alert_words._select_alert_word_additions([], [special])), [special]
            )
            self.assertEqual(
                list(alert_words._select_alert_word_additions(["Keep"], iter(["KEEP", "new"]))),
                ["new"],
            )
