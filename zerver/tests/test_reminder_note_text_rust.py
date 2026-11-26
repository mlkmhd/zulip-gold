from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from typing_extensions import override

from zerver.lib import reminders
from zerver.lib.exceptions import JsonableError


class ReminderNoteTextContractTest(SimpleTestCase):
    @override_settings(MAX_REMINDER_NOTE_LENGTH=3)
    def test_preserves_leading_spaces_and_counts_unicode_code_points(self) -> None:
        note = "\n\n 📝\ud800\u3000\u001f"
        self.assertEqual(reminders.normalize_note_text(note), " 📝\ud800")
        with (
            override_settings(MAX_REMINDER_NOTE_LENGTH=2),
            self.assertRaisesRegex(JsonableError, "Maximum reminder note length: 2 characters"),
        ):
            reminders.normalize_note_text(note)

    def test_native_selector_keeps_identity_and_validates_length(self) -> None:
        backend = reminders._rust_reminder_note_text
        if getattr(backend, "REMINDER_NOTE_TEXT_API", None) != 1:
            self.skipTest("Matching Rust reminder note text contract is not available")
        note = " 📝\ud800"
        with (
            override_settings(MAX_REMINDER_NOTE_LENGTH=3),
            patch.object(
                backend,
                "normalize_exact_reminder_note",
                wraps=backend.normalize_exact_reminder_note,
            ) as native,
        ):
            self.assertIs(reminders.normalize_note_text(note), note)
        native.assert_called_once_with(note, 3)

    def test_custom_rstrip_behavior_is_retained(self) -> None:
        class CustomNote(str):
            __slots__ = ()

            @override
            def rstrip(self, chars: str | None = None, /) -> str:
                return "\ncustom\n"

        self.assertEqual(reminders.normalize_note_text(CustomNote("ignored")), "custom\n")
