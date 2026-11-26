from datetime import datetime, timezone
from types import ModuleType
from unittest.mock import patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import topic
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.lib.types import EditHistoryEvent
from zerver.models import Message
from zerver.views.message_edit import fill_edit_history_entries


class EmptyTopicCompatibilityRustTest(SimpleTestCase):
    def test_separate_legacy_aliases_keep_case_and_identity(self) -> None:
        first = topic.maybe_rename_general_chat_to_empty_topic
        second = topic.maybe_rename_no_topic_to_empty_topic
        self.assertEqual(first("general chat"), "")
        self.assertEqual(second("(no topic)"), "")
        self.assertEqual(first("(no topic)"), "(no topic)")
        self.assertEqual(second("general chat"), "general chat")
        for text in ("General Chat", "No Topic", " موضوع \ud800 ", "✔ general chat", ""):
            with self.subTest(text=text):
                self.assertIs(first(text), text)
                self.assertIs(second(text), text)
                self.assertIs(
                    topic.maybe_rename_empty_topic_to_general_chat(text, True, True), text
                )

    def test_python_subclass_comparison_stays_unchanged(self) -> None:
        class Alias(str):
            __slots__ = ()
            __hash__ = str.__hash__

            @override
            def __eq__(self, other: object) -> bool:
                return other == "general chat"

        self.assertEqual(topic.maybe_rename_general_chat_to_empty_topic(Alias("changed")), "")

    def test_actual_edit_history_preserves_old_and_new_client_names(self) -> None:
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        edited = datetime(2026, 1, 2, tzinfo=timezone.utc)
        event: EditHistoryEvent = {
            "user_id": 8,
            "timestamp": datetime_to_timestamp(edited),
            "prev_topic": "old",
        }
        message = Message(
            id=3,
            sender_id=9,
            is_channel_message=True,
            subject="",
            date_sent=created,
            last_edit_time=edited,
            content="test",
            rendered_content="<p>test</p>",
        )
        for allows in (False, True):
            with self.subTest(allows=allows):
                with patch.object(topic, "_rust_empty_topic_compat", ModuleType("old")):
                    reference = fill_edit_history_entries([event], message, allows)
                actual = fill_edit_history_entries([event], message, allows)
                self.assertEqual(actual, reference)
                self.assertEqual(actual[0]["topic"], "" if allows else "general chat")
                self.assertEqual(actual[0]["prev_topic"], "old")
                self.assertEqual(actual[1]["topic"], "old")
        message.is_channel_message = False
        self.assertEqual(fill_edit_history_entries([], message, False)[0]["topic"], "")

    def test_current_extension_receives_named_legacy_context(self) -> None:
        native = topic._rust_empty_topic_compat
        if native is None:
            self.skipTest("requires current Rust topic compatibility contract")
        with patch.object(
            native, "format_legacy_empty_topic", wraps=native.format_legacy_empty_topic
        ) as call:
            self.assertEqual(
                topic.maybe_rename_empty_topic_to_general_chat("", True, False), "general chat"
            )
        call.assert_called_once_with("", True, False, "general chat")
