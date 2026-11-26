from contextlib import nullcontext
from unittest.mock import patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import addressee
from zerver.lib.exceptions import JsonableError
from zerver.models import Message


class ChannelTopicInputRustTest(SimpleTestCase):
    def test_exact_topic_preserves_spelling_identity_and_error_positions(self) -> None:
        backend = addressee._rust_exact_channel_topic
        witness = (
            patch.object(
                backend,
                "normalize_exact_channel_message_topic",
                wraps=backend.normalize_exact_channel_message_topic,
            )
            if backend is not None
            else nullcontext()
        )

        def outcome(topic: str) -> tuple[str, str]:
            try:
                return "topic", addressee._normalize_channel_message_topic(topic)
            except JsonableError as e:
                return "error", str(e)

        fallback = Message.EMPTY_TOPIC_FALLBACK_NAME
        unchanged = "سلام 😀"
        cases = (
            "",
            unchanged,
            " \u001fآهنگ\u3000",
            fallback,
            fallback.upper(),
            "  (no topic) ",
            "  😀\0rest",
            "🦀\ufdd0end",
            "👋\U0010ffffend",
            "\u200b invisible",
            "سلام\ud800end",
            "x\tend",
        )
        with patch.object(addressee, "_rust_channel_message_topic", None), witness as native:
            for case in cases:
                with self.subTest(topic=repr(case)):
                    with patch.object(addressee, "_rust_exact_channel_topic", None):
                        expected = outcome(case)
                    self.assertEqual(outcome(case), expected)
            self.assertIs(addressee._normalize_channel_message_topic(unchanged), unchanged)
            if native is not None:
                self.assertEqual(native.call_count, len(cases) + 1)

    def test_custom_topic_callbacks_and_subtypes_keep_python_behavior(self) -> None:
        class DerivedTopic(str):
            __slots__ = ()

            @override
            def strip(self, chars: str | None = None) -> str:
                return "New title"

        with patch.object(addressee, "_rust_channel_message_topic", None):
            self.assertEqual(
                addressee._normalize_channel_message_topic(DerivedTopic("original")), "New title"
            )
            with patch.object(
                addressee, "maybe_rename_no_topic_to_empty_topic", return_value="custom"
            ) as rewrite:
                self.assertEqual(addressee._normalize_channel_message_topic(" original "), "custom")
            rewrite.assert_called_once_with("original")
