from datetime import datetime, timezone
from types import ModuleType
from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase

from zerver.lib.timestamp import datetime_to_timestamp
from zerver.lib.types import EditHistoryEvent
from zerver.models import Message
from zerver.views import message_edit
from zerver.views.message_edit import fill_edit_history_entries


class EditHistoryAssemblyRustTest(SimpleTestCase):
    def fixture(self) -> tuple[Message, list[EditHistoryEvent]]:
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        edited = datetime(2026, 1, 3, tzinfo=timezone.utc)
        message = Message(
            id=993,
            sender_id=9,
            is_channel_message=True,
            subject="",
            date_sent=created,
            last_edit_time=edited,
            content="final",
            rendered_content="<p>new</p>",
        )
        rows: list[EditHistoryEvent] = [
            {
                "timestamp": datetime_to_timestamp(edited),
                "user_id": None,
                "prev_topic": "before",
                "prev_content": "first",
                "prev_rendered_content": "<p>before</p>",
                "prev_stream": 1,
                "stream": 2,
            },
            {"timestamp": datetime_to_timestamp(created) + 10, "user_id": 8, "prev_topic": ""},
        ]
        return message, rows

    def test_mixed_topic_content_stream_and_original_author(self) -> None:
        message, rows = self.fixture()

        def render(html: str) -> str:
            return html.replace("<p>", "<p class='plain'>")

        for allow_empty in (True, False):
            with (
                self.subTest(allow_empty=allow_empty),
                patch.object(message_edit, "render_plain_reply_links", side_effect=render) as links,
                patch.object(
                    message_edit, "highlight_html_differences", return_value="synthetic diff"
                ) as diffs,
            ):
                with patch.object(message_edit, "_rust_edit_history_assembly", ModuleType("old")):
                    expected = fill_edit_history_entries(
                        rows, message, allow_empty, plain_reply_links=True
                    )
                links.reset_mock()
                diffs.reset_mock()
                result = fill_edit_history_entries(
                    rows, message, allow_empty, plain_reply_links=True
                )
                self.assertEqual(result, expected)
                self.assertEqual([tuple(row) for row in result], [tuple(row) for row in expected])
                self.assertEqual(links.call_args_list, [call("<p>new</p>"), call("<p>before</p>")])
                diffs.assert_called_once_with(
                    "<p class='plain'>before</p>", "<p class='plain'>new</p>", 993
                )
                self.assertEqual([row["user_id"] for row in result], [None, 8, 9])
                self.assertEqual(result[0]["content_html_diff"], "synthetic diff")
                self.assertEqual(result[0]["prev_stream"], 1)
                self.assertEqual(result[-1]["topic"], "" if allow_empty else "general chat")

    def test_empty_and_malformed_history_keep_legacy_order_and_errors(self) -> None:
        message, rows = self.fixture()
        self.assertEqual(fill_edit_history_entries([], message, True)[0]["user_id"], 9)
        bad = dict(rows[0])
        del bad["prev_rendered_content"]
        with self.assertRaises(KeyError) as error:
            fill_edit_history_entries([bad], message, True)  # type: ignore[list-item]
        self.assertEqual(error.exception.args, ("prev_rendered_content",))
        rows[0]["timestamp"] -= 1
        with self.assertRaises(AssertionError):
            fill_edit_history_entries(rows, message, True)

    def test_current_extension_called_only_after_timestamp_validation(self) -> None:
        native = message_edit._rust_edit_history_assembly
        if native is None:
            self.skipTest("requires current edit history assembly")
        message, rows = self.fixture()
        with patch.object(
            native, "assemble_prepared_edit_history", wraps=native.assemble_prepared_edit_history
        ) as assemble:
            self.assertEqual(len(fill_edit_history_entries(rows, message, True)), 3)
            assemble.assert_called_once()
            rows[0]["timestamp"] -= 1
            with self.assertRaises(AssertionError):
                fill_edit_history_entries(rows, message, True)
            assemble.assert_called_once()

    def test_noncanonical_container_retains_python(self) -> None:
        class HistoryRows(list[EditHistoryEvent]):
            pass

        message, rows = self.fixture()
        native = message_edit._rust_edit_history_assembly
        render = Mock(return_value="same diff")
        with patch.object(message_edit, "highlight_html_differences", render):
            actual = fill_edit_history_entries(HistoryRows(rows), message, True)
            with patch.object(message_edit, "_rust_edit_history_assembly", None):
                self.assertEqual(actual, fill_edit_history_entries(rows, message, True))
        self.assertEqual(message_edit._rust_edit_history_assembly, native)
