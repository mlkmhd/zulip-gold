from typing import TYPE_CHECKING
from unittest.mock import patch

import orjson

from zerver.actions.alert_words import do_add_alert_words
from zerver.actions.message_flags import do_update_message_flags
from zerver.lib import native_user_message_flags
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserMessage, UserProfile

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


class MessageReceiptLifecycleRustTest(ZulipTestCase):
    def exercise_lifecycle(self, *, session: bool, direct: bool) -> None:
        sender = self.example_user("hamlet")
        reader = self.example_user("cordelia")
        other_recipient = self.example_user("othello")
        outsider = self.example_user("iago")
        alert_word = "cobaltschist"
        do_add_alert_words(reader, [alert_word])
        content = f"@**{reader.full_name}** {alert_word}"
        if direct:
            data = {
                "type": "direct",
                "to": orjson.dumps([reader.id, other_recipient.id]).decode(),
                "content": content,
            }
            channel = None
        else:
            channel = self.make_stream(
                "Receipt lifecycle", invite_only=True, history_public_to_subscribers=False
            )
            for user in (sender, reader, other_recipient):
                self.subscribe(user, channel.name)
            data = {
                "type": "channel",
                "to": orjson.dumps([channel.id]).decode(),
                "topic": "Receipt lifecycle",
                "content": content,
            }

        if session:
            self.login_user(sender)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            if session:
                send_response = self.client_post("/json/messages", data)
            else:
                send_response = self.api_post(sender, "/api/v1/messages", data)
        message_id = self.assert_json_success(send_response)["id"]
        initial = events[0]
        self.assertEqual(
            (initial["event"]["type"], initial["event"]["message"]), ("message", message_id)
        )
        initial_audience = {recipient["id"]: recipient["flags"] for recipient in initial["users"]}
        self.assertEqual(set(initial_audience), {sender.id, reader.id, other_recipient.id})
        self.assertEqual(initial_audience[reader.id], ["mentioned", "has_alert_word"])
        self.assertEqual(initial_audience[sender.id], ["read"])
        initial_rows = UserMessage.objects.filter(message_id=message_id)
        self.assertEqual(
            set(initial_rows.values_list("user_profile_id", flat=True)), set(initial_audience)
        )
        reader_row = initial_rows.get(user_profile=reader)
        self.assertEqual(reader_row.flags_list(), ["mentioned", "has_alert_word"])
        self.assertEqual(bool(reader_row.flags.is_private), direct)

        def fetch(user: UserProfile) -> "TestHttpResponse":
            params = {"message_ids": orjson.dumps([message_id]).decode()}
            if session:
                self.login_user(user)
                return self.client_get("/json/messages", params)
            return self.api_get(user, "/api/v1/messages", params)

        def compare_fetch(user: UserProfile) -> list[dict[str, object]]:
            response = fetch(user)
            found = self.assert_json_success(response)["messages"]
            with (
                patch.object(native_user_message_flags, "_backend", None),
                patch.object(native_user_message_flags, "_prepared_fetch_backend", None),
            ):
                original = fetch(user)
            self.assertEqual(response.content, original.content)
            return found

        before = compare_fetch(reader)
        self.assert_length(before, 1)
        self.assertEqual(before[0]["flags"], ["mentioned", "has_alert_word"])
        self.assertEqual(compare_fetch(outsider), [])

        do_update_message_flags(reader, "add", "read", [message_id])
        do_update_message_flags(reader, "add", "starred", [message_id])
        if channel is not None:
            self.unsubscribe(other_recipient, channel.name)
        if session:
            self.login_user(sender)
        final_content = "Edited without any mentions or alert words."
        with self.capture_send_event_calls(expected_num_events=1) as events:
            if session:
                edit_response = self.client_patch(
                    f"/json/messages/{message_id}", {"content": final_content}
                )
            else:
                edit_response = self.api_patch(
                    sender, f"/api/v1/messages/{message_id}", {"content": final_content}
                )
        self.assert_json_success(edit_response)
        edited = events[0]
        self.assertEqual(
            (edited["event"]["type"], edited["event"]["message_id"]), ("update_message", message_id)
        )
        edited_audience = {recipient["id"]: recipient["flags"] for recipient in edited["users"]}
        expected_audience = (
            {sender.id, reader.id, other_recipient.id} if direct else {sender.id, reader.id}
        )
        self.assertEqual(set(edited_audience), expected_audience)
        self.assertEqual(edited_audience[reader.id], ["read", "starred"])

        self.assertEqual(
            set(initial_rows.values_list("user_profile_id", flat=True)), set(initial_audience)
        )
        reader_row.refresh_from_db()
        self.assertEqual(reader_row.flags_list(), ["read", "starred"])
        self.assertEqual(bool(reader_row.flags.is_private), direct)

        after = compare_fetch(reader)
        self.assert_length(after, 1)
        self.assertEqual(after[0]["flags"], ["read", "starred"])
        self.assertEqual(after[0]["id"], message_id)
        self.assertIn(final_content, str(after[0]["content"]))
        self.assertEqual(compare_fetch(outsider), [])
        if channel is not None:
            self.assertEqual(compare_fetch(other_recipient), [])

    def test_private_channel_through_basic_api(self) -> None:
        self.exercise_lifecycle(session=False, direct=False)

    def test_group_direct_message_through_signed_session(self) -> None:
        self.exercise_lifecycle(session=True, direct=True)
