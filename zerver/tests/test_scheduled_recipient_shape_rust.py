from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import scheduled_messages
from zerver.lib.exceptions import JsonableError


class ScheduledRecipientShapeContractTest(SimpleTestCase):
    def test_invalid_and_unknown_shapes_still_use_reference_rules(self) -> None:
        parse = scheduled_messages.parse_scheduled_message_recipient
        with patch.object(scheduled_messages, "_rust_scheduled_message_recipient", None):
            self.assertEqual(parse("private", []), ("private", []))
            self.assertEqual(parse("custom-type", [14, 14]), ("private", [14]))
            with self.assertRaisesRegex(JsonableError, "Invalid data type for channel ID"):
                parse("channel", [14])
            with self.assertRaisesRegex(JsonableError, "Invalid data type for recipients"):
                parse("direct", 14)

    def test_natively_classified_shape_calls_existing_recipient_extractor(self) -> None:
        backend = scheduled_messages._rust_scheduled_recipient_shape
        if getattr(backend, "SCHEDULED_RECIPIENT_SHAPE_API", None) != 1:
            self.skipTest("Matching Rust scheduled recipient shape contract is not available")
        original = [88, 7, 88]
        with (
            patch.object(
                backend,
                "classify_scheduled_message_recipient",
                wraps=backend.classify_scheduled_message_recipient,
            ) as classify,
            patch.object(
                scheduled_messages, "extract_direct_message_recipient_ids", return_value=[7, 88]
            ) as extract,
        ):
            self.assertEqual(
                scheduled_messages.parse_scheduled_message_recipient("direct", original),
                ("private", [7, 88]),
            )
        classify.assert_called_once_with("direct", 3)
        extract.assert_called_once_with(original)
