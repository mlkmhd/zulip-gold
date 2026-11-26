from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import drafts, message
from zerver.lib.exceptions import JsonableError


class DraftTopicTextContractTest(SimpleTestCase):
    def test_cutoff_before_nul_validation_and_original_identity(self) -> None:
        with patch.object(message, "MAX_TOPIC_NAME_LENGTH", 6):
            self.assertEqual(drafts.normalize_draft_topic("آ😀\ud800rest\0"), "آ😀\ud800...")
            with self.assertRaisesRegex(JsonableError, "Topic must not contain null bytes"):
                drafts.normalize_draft_topic("آ\0rest")
            topic = "آ😀\ud800"
            self.assertIs(drafts.normalize_draft_topic(topic), topic)

    def test_custom_truncator_preserves_supported_dispatch(self) -> None:
        backend = drafts._rust_draft_topic_text
        if getattr(backend, "DRAFT_TOPIC_TEXT_API", None) != 1:
            self.skipTest("Matching Rust draft topic contract is not available")
        with patch.object(
            backend, "normalize_exact_draft_topic", wraps=backend.normalize_exact_draft_topic
        ) as native:
            self.assertEqual(drafts.normalize_draft_topic("Topic"), "Topic")
            with (
                patch.object(drafts, "_rust_draft_text", None),
                patch.object(drafts, "truncate_topic", return_value="custom") as truncate,
            ):
                self.assertEqual(drafts.normalize_draft_topic("Topic"), "custom")
            truncate.assert_called_once_with("Topic")
        native.assert_called_once_with("Topic", message.MAX_TOPIC_NAME_LENGTH)
