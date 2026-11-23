from importlib import import_module
from types import ModuleType, SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson
from django.db import connection
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext
from typing_extensions import override

from zerver.actions.direct_message_groups import do_set_direct_message_group_pin
from zerver.lib import native_dm_pin
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Subscription
from zerver.models.recipients import get_or_create_direct_message_group


def extension() -> ModuleType:
    try:
        module = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(module, "set_existing_dm_pin", None)):
        raise SkipTest("Rust extension does not support existing direct-message pins")
    return module


class DirectMessagePinRustTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.peer = self.example_user("cordelia")
        group = get_or_create_direct_message_group(sorted((self.owner.id, self.peer.id)))
        recipient_id = group.recipient_id
        assert recipient_id is not None
        self.row = Subscription.objects.get(user_profile=self.owner, recipient_id=recipient_id)
        self.peer_row = Subscription.objects.get(user_profile=self.peer, recipient_id=recipient_id)

    def test_basic_and_session_preserve_the_owner_and_event(self) -> None:
        backend = extension()
        witness = Mock(wraps=backend.set_existing_dm_pin)
        self.login_user(self.owner)
        form = {
            "user_ids": orjson.dumps([self.peer.id, self.owner.id, self.peer.id]).decode(),
            "pinned": "true",
        }

        def run(mode: SimpleNamespace | None, session: bool) -> tuple[int, bytes]:
            Subscription.objects.filter(id=self.row.id).update(pin_to_top=False)
            with (
                patch.object(native_dm_pin, "_backend", mode),
                self.capture_send_event_calls(expected_num_events=1) as events,
            ):
                response = (
                    self.client_post("/json/users/me/dm_conversations/pin", form)
                    if session
                    else self.api_post(self.owner, "/api/v1/users/me/dm_conversations/pin", form)
                )
            self.assert_json_success(response)
            self.assertEqual(
                events,
                [
                    {
                        "event": {
                            "type": "direct_message_conversation",
                            "user_ids": [self.peer.id],
                            "pinned": True,
                        },
                        "users": [self.owner.id],
                    }
                ],
            )
            self.row.refresh_from_db()
            self.peer_row.refresh_from_db()
            self.assertTrue(self.row.pin_to_top)
            self.assertFalse(self.peer_row.pin_to_top)
            return response.status_code, response.content

        for session in (False, True):
            legacy = run(None, session)
            self.assertEqual(run(SimpleNamespace(set_existing_dm_pin=witness), session), legacy)
            self.assertEqual(run(SimpleNamespace(), session), legacy)
        self.assertEqual(witness.call_count, 2)
        for call in witness.call_args_list:
            self.assertEqual(call.args[1], self.owner.id)
            self.assertEqual(set(call.args[2]), {self.owner.id, self.peer.id})
            self.assertIs(call.args[3], True)

    def test_one_statement_and_legacy_model_hooks(self) -> None:
        backend = extension()
        witness = Mock(wraps=backend.set_existing_dm_pin)
        native = SimpleNamespace(set_existing_dm_pin=witness)

        def measure(candidate: SimpleNamespace | None, change: bool) -> int:
            Subscription.objects.filter(id=self.row.id).update(pin_to_top=False)
            with (
                patch.object(native_dm_pin, "_backend", candidate),
                CaptureQueriesContext(connection) as queries,
                self.capture_send_event_calls(expected_num_events=int(change)),
            ):
                do_set_direct_message_group_pin(self.owner, [self.peer.id], pinned=change)
            self.row.refresh_from_db()
            self.assertEqual(self.row.pin_to_top, change)
            return sum(
                any(
                    name in query["sql"]
                    for name in ("zerver_subscription", "zerver_huddle", "zerver_recipient")
                )
                for query in queries
            )

        self.assertEqual(measure(None, True), 4)
        self.assertEqual(measure(None, False), 3)
        self.assertEqual(measure(native, True), 1)
        self.assertEqual(measure(native, False), 1)
        self.assertEqual(witness.call_count, 2)

        hook = Mock()
        post_save.connect(hook, sender=Subscription, weak=False)
        try:
            self.assertEqual(measure(native, True), 4)
        finally:
            post_save.disconnect(hook, sender=Subscription)
        hook.assert_called_once()
        self.assertEqual(witness.call_count, 2)

    def test_native_write_rolls_back_without_replaying(self) -> None:
        backend = extension()
        witness = Mock(wraps=backend.set_existing_dm_pin)
        with (
            patch.object(native_dm_pin, "_backend", SimpleNamespace(set_existing_dm_pin=witness)),
            patch(
                "zerver.actions.direct_message_groups.send_event_on_commit",
                side_effect=RuntimeError("synthetic abort after native pin"),
            ),
            self.capture_send_event_calls(expected_num_events=0),
            self.assertRaisesMessage(RuntimeError, "synthetic abort after native pin"),
            self.artificial_transaction_savepoint(),
        ):
            do_set_direct_message_group_pin(self.owner, [self.peer.id], pinned=True)
        self.row.refresh_from_db()
        self.assertFalse(self.row.pin_to_top)
        witness.assert_called_once()
