from typing import cast
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import reminders
from zerver.lib.types import UserDisplayRecipient


class ReminderRecipientLabelsContractTest(SimpleTestCase):
    def test_receiver_labels_match_code_point_order_and_skip_sender(self) -> None:
        recipients = cast(
            list[UserDisplayRecipient],
            [
                {"id": 99, "full_name": "sender"},
                {"id": 3, "full_name": "😀"},
                {"id": 2, "full_name": "\ud800"},
                {"id": 8, "full_name": "آ"},
                {"id": 9, "full_name": "آ"},
            ],
        )
        expected = ["@_**آ|8**", "@_**آ|9**", "@_**\ud800|2**", "@_**😀|3**"]
        self.assertEqual(reminders._reminder_recipient_mentions(99, recipients), expected)

    def test_matching_wheel_dispatch_and_custom_formatter(self) -> None:
        backend = reminders._rust_reminder_recipient_labels
        if getattr(backend, "REMINDER_RECIPIENT_LABELS_API", None) != 1:
            self.skipTest("Matching Rust reminder recipient labels contract is not available")
        recipients = cast(list[UserDisplayRecipient], [{"id": 4, "full_name": "Nima"}])
        with patch.object(
            backend,
            "exact_reminder_recipient_labels",
            wraps=backend.exact_reminder_recipient_labels,
        ) as native:
            self.assertEqual(
                reminders._reminder_recipient_mentions(19, recipients), ["@_**Nima|4**"]
            )
            with (
                patch.object(reminders, "_rust_reminder_context", None),
                patch.object(reminders, "silent_mention_syntax_for_user", return_value="custom"),
            ):
                self.assertEqual(reminders._reminder_recipient_mentions(19, recipients), ["custom"])
        native.assert_called_once_with(19, recipients)
