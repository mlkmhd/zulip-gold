from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from zerver.lib import drafts
from zerver.lib.exceptions import JsonableError


class DraftBodyTextContractTest(SimpleTestCase):
    @override_settings(MAX_MESSAGE_LENGTH=23)
    def test_trim_then_validate_before_truncation(self) -> None:
        original = " آ😀\ud800"
        self.assertIs(drafts.normalize_draft_body(original), original)
        self.assertEqual(drafts.normalize_draft_body("\n\n" + original + "\u3000\u001f"), original)
        self.assertEqual(drafts.normalize_draft_body(original * 12), " آ😀\n[message truncated]")
        with self.assertRaisesRegex(JsonableError, "Message must not contain null bytes"):
            drafts.normalize_draft_body("x" * 50 + "\0")
        with self.assertRaisesRegex(JsonableError, "Message must not be empty"):
            drafts.normalize_draft_body("\n \u3000\u001f")

    def test_ordinary_body_uses_matching_wheel_and_custom_truncate_uses_python(self) -> None:
        backend = drafts._rust_draft_body_text
        if getattr(backend, "DRAFT_BODY_TEXT_API", None) != 1:
            self.skipTest("Matching Rust draft body contract is not available")
        with patch.object(
            backend, "normalize_exact_draft_body", wraps=backend.normalize_exact_draft_body
        ) as native:
            self.assertEqual(drafts.normalize_draft_body("Content"), "Content")
            with (
                patch.object(drafts, "_rust_draft_text", None),
                patch.object(drafts, "normalize_body", return_value="custom") as custom,
            ):
                self.assertEqual(drafts.normalize_draft_body("Content"), "custom")
            custom.assert_called_once_with("Content")
        native.assert_called_once()
