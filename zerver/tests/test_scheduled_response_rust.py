from django.test import SimpleTestCase

from zerver.models import scheduled_jobs


class PreparedScheduledResponseRustTest(SimpleTestCase):
    def test_exact_fields_preserve_order_and_python_objects(self) -> None:
        backend = scheduled_jobs._rust_prepared_scheduled_response
        if backend is None:
            self.skipTest("Prepared scheduled response extension is unavailable")
        native = backend.format_prepared_scheduled_response
        content = "متن\ud800"
        rendered = "<p>Rendered</p>"
        people = [7, 7, 3]
        topic = "عنوان"
        values = (
            (11, people, "private", content, rendered, None, -1, False, None),
            (12, 4, "stream", content, rendered, topic, 0, True, None),
            (13, people, "private", content, rendered, None, 1, False, 2**70),
        )
        direct, channel, reminder = [native(value) for value in values]
        self.assertEqual(
            list(direct),
            [
                "scheduled_message_id",
                "to",
                "type",
                "content",
                "rendered_content",
                "scheduled_delivery_timestamp",
                "failed",
            ],
        )
        self.assertEqual(
            list(channel),
            [
                "scheduled_message_id",
                "to",
                "type",
                "content",
                "rendered_content",
                "topic",
                "scheduled_delivery_timestamp",
                "failed",
            ],
        )
        self.assertEqual(
            list(reminder), ["reminder_id", *list(direct)[1:], "reminder_target_message_id"]
        )
        self.assertIs(direct["to"], people)
        self.assertIs(reminder["to"], people)
        self.assertIs(channel["content"], content)
        self.assertIs(channel["topic"], topic)
        self.assertEqual(reminder["reminder_target_message_id"], 2**70)
        self.assertIsNone(native((11, [True], "private", content, rendered, None, 1, False, None)))
