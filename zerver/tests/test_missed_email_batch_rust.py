from random import Random
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.models import ScheduledMessageNotificationEmail
from zerver.worker import missedmessage_emails


class MissedEmailBatchRustTest(SimpleTestCase):
    def test_locked_rows_keep_order_last_duplicate_and_all_deletion_ids(self) -> None:
        backend = missedmessage_emails._missed_email_backend
        if backend is None or not hasattr(backend, "group_prepared_missed_email_rows"):
            self.skipTest("No prepared missed-email batch ABI")
        rng = Random(7264)
        rows = [
            ScheduledMessageNotificationEmail(
                id=index,
                user_profile_id=rng.randrange(10),
                message_id=rng.randrange(30),
                trigger=f"trigger-{index}-فارسی",
                mentioned_user_group_id=index if index % 2 else None,
            )
            for index in range(1200)
        ]
        for batch in ([], rows[:1], rows[:12], rows):
            with (
                self.subTest(length=len(batch)),
                patch.object(
                    backend,
                    "group_prepared_missed_email_rows",
                    wraps=backend.group_prepared_missed_email_rows,
                ) as witness,
            ):
                actual_ids, actual_groups = missedmessage_emails._group_locked_notification_rows(
                    batch
                )
                witness.assert_called_once_with(
                    [(row.user_profile_id, row.message_id) for row in batch]
                )
                with patch.object(missedmessage_emails, "_missed_email_backend", None):
                    expected_ids, expected_groups = (
                        missedmessage_emails._group_locked_notification_rows(batch)
                    )
                self.assertEqual(actual_ids, expected_ids)
                self.assertEqual(
                    [
                        (recipient, list(messages.items()))
                        for recipient, messages in actual_groups.items()
                    ],
                    [
                        (recipient, list(messages.items()))
                        for recipient, messages in expected_groups.items()
                    ],
                )
