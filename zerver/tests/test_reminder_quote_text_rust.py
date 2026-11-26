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
            patch.object(reminders, "_rust_prepared_reminder_body", None),
            patch.object(reminders, "_rust_exact_reminder_quote", None),
            patch.object(reminders, "_rust_reminder_quote", None),
        ):
            expected = reminders.get_reminder_formatted_content(message, viewer)
        backend = reminders._rust_exact_reminder_quote
        if getattr(backend, "REMINDER_QUOTE_TEXT_API", None) == 1:
            with (
                patch.object(reminders, "_rust_prepared_reminder_body", None),
                patch.object(
                    backend,
                    "append_exact_reminder_quote",
                    wraps=backend.append_exact_reminder_quote,
                ) as quote,
            ):
                actual = reminders.get_reminder_formatted_content(message, viewer)
            quote.assert_called_once()
        else:
            actual = reminders.get_reminder_formatted_content(message, viewer)
        self.assertEqual(actual, expected)
        self.assertTrue(actual.endswith("text\n" + chr(96) * 5))

    def test_prepared_body_matches_widget_and_truncated_quote_bytes(self) -> None:
        backend = reminders._rust_prepared_reminder_body
        if backend is None:
            self.skipTest("Matching native reminder body contract is not available")

        viewer = self.example_user("hamlet")
        sources = (
            self.send_stream_message(viewer, "Verona", "متن و `inline`\n" + "🙂" * 200),
            self.send_stream_message(viewer, "Verona", "/poll Main question\n- one\n- two"),
            self.send_personal_message(viewer, viewer, "/todo Tasks\n- پیام"),
            self.send_personal_message(self.example_user("othello"), viewer, "A direct reminder"),
        )
        for message_id in sources:
            with self.subTest(message_id=message_id), self.settings(MAX_MESSAGE_LENGTH=140):
                message = Message.objects.get(id=message_id)
                with (
                    patch.object(reminders, "_rust_prepared_reminder_body", None),
                    patch.object(reminders, "_rust_exact_reminder_quote", None),
                    patch.object(reminders, "_rust_reminder_quote", None),
                ):
                    expected = reminders.get_reminder_formatted_content(message, viewer, "یادداشت")
                with patch.object(
                    backend,
                    "format_prepared_reminder_body",
                    wraps=backend.format_prepared_reminder_body,
                ) as render:
                    actual = reminders.get_reminder_formatted_content(message, viewer, "یادداشت")
                render.assert_called_once()
                self.assertEqual(actual.encode(), expected.encode())
                if message.content.startswith(("/poll", "/todo")):
                    self.assertIsNone(render.call_args.args[2])
                else:
                    self.assertEqual(render.call_args.args[2], (message.content, 140))

    def test_prepared_body_supports_lone_surrogates_and_declines_invalid_quote(self) -> None:
        backend = reminders._rust_prepared_reminder_body
        if backend is None:
            self.skipTest("Matching native reminder body contract is not available")
        heading = "متن\ud800\n\n"
        caption = "شرح\udfff"
        message = "\ud801🙂" + "nested" * 30
        prefix = heading + caption + "\n"
        fence = get_unused_fence(message)
        overhead = len(prefix + MESSAGE_QUOTE_BODY.format(fence=fence, content=""))
        for limit in (-5, 25, overhead, overhead + 70, 500):
            with self.subTest(limit=limit):
                expected = prefix + MESSAGE_QUOTE_BODY.format(
                    fence=fence,
                    content=truncate_content(message, limit - overhead, "\n[message truncated]"),
                )
                self.assertEqual(
                    backend.format_prepared_reminder_body(heading, caption, (message, limit)),
                    expected,
                )
        self.assertEqual(
            backend.format_prepared_reminder_body(heading, caption, None), heading + caption
        )
        self.assertIsNone(backend.format_prepared_reminder_body(heading, caption, (message, True)))
        self.assertIsNone(backend.format_prepared_reminder_body(heading, caption, (message, 2**80)))
