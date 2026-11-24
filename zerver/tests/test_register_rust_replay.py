import time
from collections import Counter
from datetime import datetime, timedelta
from importlib import import_module
from os import environ
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson
import time_machine
from django.utils.timezone import now as timezone_now

from zerver.actions.channel_folders import check_add_channel_folder, do_change_channel_folder_name
from zerver.actions.custom_profile_fields import try_add_realm_custom_profile_field
from zerver.actions.direct_message_groups import do_set_direct_message_group_pin
from zerver.actions.message_flags import do_update_message_flags
from zerver.actions.navigation_views import do_add_navigation_view
from zerver.actions.reminders import schedule_reminder_for_message
from zerver.actions.saved_snippets import do_create_saved_snippet
from zerver.actions.scheduled_messages import check_schedule_message
from zerver.lib import native_register_state
from zerver.lib.drafts import DraftData, do_create_drafts
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import stub_event_queue_user_events
from zerver.models import CustomProfileField, UserProfile
from zerver.models.clients import get_client
from zerver.tornado.django_api import EventQueueData
from zerver.tornado.event_queue import (
    allocate_client_descriptor,
    clear_client_event_queues_for_testing,
)

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


def profile_pruning_extension() -> ModuleType:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(backend, "prune_register_custom_profile_fields", None)):
        raise SkipTest("Rust extension does not support profile field replay")
    return backend


class RegisterRustReplayTest(ZulipTestCase):
    def test_prune_custom_profile_field_rows_preserves_order_and_aliases(self) -> None:
        native = profile_pruning_extension().prune_register_custom_profile_fields

        fields = [{"id": 1}, {"id": 3}, {"id": 1}]
        retained = {"value": "سلام 🙂", "rendered_value": None}
        profile_data = {"3": retained, "2": {"value": "obsolete"}, "1": {"value": "keep"}}
        card = {"profile_data": profile_data, "is_active": True}
        other = {"is_bot": True}
        raw = {5: card, 7: other}

        unexpected = {"profile_data": {" 1 ": {"value": "legacy"}}}
        self.assertFalse(native({**raw, 9: unexpected}, fields))
        self.assertFalse(native({**raw, 10: {"profile_data": profile_data}}, fields))
        self.assertFalse(native(raw, [{"id": True}]))
        self.assertEqual(list(profile_data), ["3", "2", "1"])

        self.assertTrue(native(raw, fields))
        self.assertIs(raw[5], card)
        self.assertIs(card["profile_data"], profile_data)
        self.assertIs(profile_data["3"], retained)
        self.assertEqual(list(profile_data), ["3", "1"])
        self.assertIs(raw[7], other)

    def test_register_profile_field_pruning_preserves_both_routes_and_rollback(self) -> None:
        backend = profile_pruning_extension()
        native = backend.prune_register_custom_profile_fields
        enabled = environ.get("ZULIP_RUST_REGISTER_PROFILE_PRUNE") == "1"
        owner = self.example_user("hamlet")
        peer = self.example_user("cordelia")
        first, obsolete, last = [
            try_add_realm_custom_profile_field(
                owner.realm, name, field_type=CustomProfileField.SHORT_TEXT
            )
            for name in ("Fixture first", "Fixture obsolete", "Fixture last")
        ]
        self.set_user_custom_profile_data(
            peer,
            [
                {"id": first.id, "value": "سلام 🙂"},
                {"id": obsolete.id, "value": "After snapshot only"},
                {"id": last.id, "value": "Keep this value"},
            ],
        )
        self.set_user_custom_profile_data(owner, [{"id": obsolete.id, "value": "Remove this too"}])
        fields = [
            field.as_dict()
            for field in CustomProfileField.objects.filter(realm=owner.realm)
            .exclude(id=obsolete.id)
            .order_by("order", "id")
        ]
        event = {"id": 4, "type": "custom_profile_fields", "fields": fields}
        params = {"event_types": orjson.dumps(["realm_user", "custom_profile_fields"]).decode()}
        queue = EventQueueData(queue_id="15:21", idle_queue_timeout_secs=600)
        self.login_user(owner)

        def request(session: bool, *, replay: bool = True) -> tuple[int, bytes]:
            with stub_event_queue_user_events(queue, [event] if replay else []):
                result = (
                    self.client_post("/json/register", params)
                    if session
                    else self.api_post(owner, "/api/v1/register", params)
                )
            self.assert_json_success(result)
            return result.status_code, result.content

        def cards(content: bytes) -> dict[int, dict[str, Any]]:
            return {row["user_id"]: row for row in orjson.loads(content)["realm_users"]}

        accepted: list[bool] = []

        def witness(raw: dict[int, Any], received_fields: object) -> bool:
            profile = raw[peer.id]["profile_data"]
            before = list(profile)
            self.assertIn(str(obsolete.id), before)
            retained = profile[str(first.id)]
            result: bool = native(raw, received_fields)
            accepted.append(result)
            self.assertIs(raw[peer.id]["profile_data"], profile)
            self.assertIs(profile[str(first.id)], retained)
            self.assertEqual(list(profile), [key for key in before if key != str(obsolete.id)])
            return result

        def decline(raw: dict[int, Any], received_fields: object) -> bool:
            profile = raw[peer.id]["profile_data"]
            before = list(profile.items())
            # A late row whose numeric spelling Python allows must reject
            # the whole native preflight, including earlier candidate edits.
            shadow = {**raw, -1: {"profile_data": {f" {first.id} ": {"value": "unusual"}}}}
            self.assertFalse(native(shadow, received_fields))
            self.assertEqual(list(profile.items()), before)
            self.assertIn(str(obsolete.id), profile)
            return False

        with time_machine.travel(timezone_now() + timedelta(seconds=1), tick=False):
            for session in (False, True):
                with self.subTest(session=session):
                    initial = cards(request(session, replay=False)[1])
                    old_profile = initial[peer.id]["profile_data"]
                    self.assertIn(str(obsolete.id), old_profile)
                    with patch.object(native_register_state, "_profile_prune_backend", None):
                        expected = request(session)
                    actual_payload = orjson.loads(expected[1])
                    self.assertEqual(actual_payload["last_event_id"], 4)
                    self.assertEqual(actual_payload["custom_profile_fields"], fields)
                    result = cards(expected[1])
                    self.assertNotIn(str(obsolete.id), result[owner.id]["profile_data"])
                    self.assertEqual(
                        list(result[peer.id]["profile_data"].items()),
                        [
                            (key, value)
                            for key, value in old_profile.items()
                            if key != str(obsolete.id)
                        ],
                    )

                    accepted.clear()
                    with patch.object(
                        backend, "prune_register_custom_profile_fields", side_effect=witness
                    ):
                        self.assertEqual(request(session), expected)
                    self.assertEqual(accepted, [True] if enabled else [])

                    reject = Mock(side_effect=decline)
                    for candidate in (
                        SimpleNamespace(partition_register_users=backend.partition_register_users),
                        SimpleNamespace(prune_register_custom_profile_fields=reject),
                    ):
                        with patch.object(
                            native_register_state, "_profile_prune_backend", candidate
                        ):
                            self.assertEqual(request(session), expected)
                    reject.assert_called_once()

    def register_with_interleaving(self, *, session: bool) -> None:
        owner = self.example_user("hamlet")
        peer = self.example_user("cordelia")
        other = self.example_user("othello")
        initial_message = self.send_personal_message(owner, peer, "Before registration")
        private_other = do_create_saved_snippet("Other member", "Other member's snippet", other)
        if session:
            self.login_user(owner)
        clear_client_event_queues_for_testing()
        self.addCleanup(clear_client_event_queues_for_testing)
        kinds = [
            "message",
            "update_message_flags",
            "direct_message_conversation",
            "recent_private_conversations",
            "drafts",
            "saved_snippets",
            "navigation_view",
            "navigation_views",
            "scheduled_messages",
            "reminders",
            "channel_folder",
            "channel_folders",
        ]
        client = allocate_client_descriptor(
            {
                "user_profile_id": owner.id,
                "realm_id": owner.realm_id,
                "event_types": kinds,
                "client_type_name": "website",
                "apply_markdown": True,
                "client_gravatar": False,
                "slim_presence": True,
                "all_public_streams": False,
                "queue_timeout": 600,
                "last_connection_time": time.time(),
                "narrow": [],
                "bulk_message_deletion": True,
                "stream_typing_notifications": True,
                "user_settings_object": False,
                "pronouns_field_type_supported": True,
                "linkifier_url_template": True,
                "user_list_incomplete": False,
                "include_deactivated_groups": False,
                "archived_channels": False,
                "simplified_presence_events": False,
                "individual_emoji_changes": True,
            }
        )
        queue = EventQueueData(queue_id=client.event_queue.id, idle_queue_timeout_secs=600)
        logical_time = datetime.fromisoformat("2030-01-21T12:00:00+00:00")
        website = get_client("website")
        arrived: list[dict[str, Any]] = []
        new_message = initial_message

        def receive(user: UserProfile, queue_id: str, last_event_id: int) -> list[dict[str, Any]]:
            nonlocal new_message
            self.assertEqual((user.id, queue_id, last_event_id), (owner.id, queue.queue_id, -1))
            self.assertEqual(client.event_queue.contents(), [])
            with self.captureOnCommitCallbacks(execute=True):
                do_create_drafts(
                    [DraftData(type="private", to=[peer.id], topic="", content="Draft")], owner
                )
                do_create_saved_snippet("Mine", "Owner's snippet", owner)
                do_create_saved_snippet("Not mine", "A different member's update", other)
                do_add_navigation_view(owner, "inbox", True, "Pinned inbox")
                check_schedule_message(
                    owner,
                    website,
                    "private",
                    [peer.id],
                    None,
                    "Scheduled",
                    logical_time,
                    owner.realm,
                )
                schedule_reminder_for_message(
                    owner, website, initial_message, logical_time, note=""
                )
                folder = check_add_channel_folder(owner.realm, "Scratch", "", acting_user=owner)
                do_change_channel_folder_name(folder, "Channels", acting_user=owner)
                new_message = self.send_personal_message(
                    peer,
                    owner,
                    "Arrived during registration",
                    skip_capture_on_commit_callbacks=True,
                )
                do_update_message_flags(owner, "add", "read", [new_message])
                do_update_message_flags(owner, "remove", "read", [new_message])
                do_set_direct_message_group_pin(owner, [peer.id], pinned=True)

            # Like the real queue transport, return decoded JSON rather than
            # passing Tornado's event container or sharing mutable objects.
            queued: list[dict[str, Any]] = orjson.loads(orjson.dumps(client.event_queue.contents()))
            arrived.extend(queued)
            return queued

        data = {"event_types": orjson.dumps(kinds).decode()}

        def register() -> "TestHttpResponse":
            if session:
                return self.client_post("/json/register", data)
            return self.api_post(owner, "/api/v1/register", data)

        # Run the real Django register view and database snapshot. Only the
        # queue transport is replaced: an actual in-process Tornado client
        # receives normal events while the request is between the two reads.
        with (
            patch("zerver.lib.events.request_event_queue", return_value=queue),
            patch("zerver.lib.events.get_user_events", side_effect=receive) as events_read,
        ):
            raced = self.assert_json_success(register())
        events_read.assert_called_once()
        counts = Counter(event["type"] for event in arrived)
        self.assertEqual(
            counts,
            {
                "drafts": 1,
                "saved_snippets": 1,
                "navigation_view": 1,
                "scheduled_messages": 1,
                "reminders": 1,
                "channel_folder": 2,
                "message": 1,
                "update_message_flags": 2,
                "direct_message_conversation": 1,
            },
        )
        self.assertEqual(raced.pop("last_event_id"), arrived[-1]["id"])
        self.assertEqual([entry["title"] for entry in raced["saved_snippets"]], ["Mine"])
        self.assertEqual([entry["content"] for entry in raced["drafts"]], ["Draft"])
        self.assertEqual([entry["name"] for entry in raced["navigation_views"]], ["Pinned inbox"])
        self.assertEqual([entry["name"] for entry in raced["channel_folders"]], ["Channels"])
        self.assert_length(raced["reminders"], 1)
        self.assert_length(raced["scheduled_messages"], 1)
        self.assertEqual(
            raced["recent_private_conversations"][0],
            {"user_ids": [peer.id], "max_message_id": new_message, "pinned": True},
        )
        owner_unread = [
            message_id
            for conversation in raced["unread_msgs"]["pms"]
            if conversation["other_user_id"] == peer.id
            for message_id in conversation["unread_message_ids"]
        ]
        self.assertIn(new_message, owner_unread)

        with stub_event_queue_user_events(queue, []):
            fresh = self.assert_json_success(register())
        self.assertEqual(fresh.pop("last_event_id"), -1)
        self.assertEqual(raced, fresh)

        with stub_event_queue_user_events(queue, []):
            other_payload = self.assert_json_success(self.api_post(other, "/api/v1/register", data))
        self.assertCountEqual(
            [entry["title"] for entry in other_payload["saved_snippets"]],
            [private_other.title, "Not mine"],
        )
        self.assertEqual(other_payload["drafts"], [])
        self.assertEqual(other_payload["navigation_views"], [])
        self.assertEqual(other_payload["scheduled_messages"], [])
        self.assertEqual(other_payload["reminders"], [])

    def test_api_register_reconciles_the_mixed_queue(self) -> None:
        self.register_with_interleaving(session=False)

    def test_session_register_reconciles_the_mixed_queue(self) -> None:
        self.register_with_interleaving(session=True)
