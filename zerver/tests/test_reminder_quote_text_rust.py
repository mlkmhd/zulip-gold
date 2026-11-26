from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import reminders
from zerver.lib.compose_reply import MESSAGE_QUOTE_BODY
from zerver.lib.markdown.fenced_code import get_unused_fence
from zerver.lib.message import truncate_content
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message


class ReminderQuoteTextContractTest(SimpleTestCase):
    def test_exact_native_quote_matches_python_boundaries(self) -> None:
        backend = reminders._rust_exact_reminder_quote
        if getattr(backend, "REMINDER_QUOTE_TEXT_API", None) != 1:
            self.skipTest("Matching Rust reminder text contract is not available")
        for prefix in ("short:\n", "فارسی\ud800:\n"):
            for text in ("", "🙂\n\udfff", "long" * 50, "~~~\n" + chr(96) * 7 + " nested"):
                fence = get_unused_fence(text)
                overhead = len(prefix + MESSAGE_QUOTE_BODY.format(fence=fence, content=""))
                for limit in (-5, 0, overhead, overhead + 4, overhead + 25, 500):
                    expected = prefix + MESSAGE_QUOTE_BODY.format(
                        fence=fence,
                        content=truncate_content(text, limit - overhead, "\n[message truncated]"),
                    )
                    self.assertEqual(
                        backend.append_exact_reminder_quote(prefix, text, limit), expected
                    )


class ReminderQuoteTextDispatchTest(ZulipTestCase):
    def test_prepared_channel_reminder_preserves_the_text_and_fence(self) -> None:
        viewer = self.example_user("hamlet")
        source = self.send_stream_message(viewer, "Verona", "Example\n" + chr(96) * 4 + "\ntext")
        message = Message.objects.get(id=source)
        with (
            patch.object(reminders, "_rust_exact_reminder_quote", None),
            patch.object(reminders, "_rust_reminder_quote", None),
        ):
            expected = reminders.get_reminder_formatted_content(message, viewer)
        backend = reminders._rust_exact_reminder_quote
        if getattr(backend, "REMINDER_QUOTE_TEXT_API", None) == 1:
            with patch.object(
                backend, "append_exact_reminder_quote", wraps=backend.append_exact_reminder_quote
            ) as quote:
                actual = reminders.get_reminder_formatted_content(message, viewer)
            quote.assert_called_once()
        else:
            actual = reminders.get_reminder_formatted_content(message, viewer)
        self.assertEqual(actual, expected)
        self.assertTrue(actual.endswith("text\n" + chr(96) * 5))
