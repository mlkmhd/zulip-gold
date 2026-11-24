from importlib import import_module
from os import environ
from random import Random
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson

from zerver.lib import native_recent_conversation_replay, native_recent_conversations
from zerver.lib.events import apply_events
from zerver.lib.message import RecentPrivateConversationDict
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import stub_event_queue_user_events
from zerver.tornado.django_api import EventQueueData

METHODS = ("replay_recent_conversation_message", "replay_recent_conversation_pin")


def extension() -> ModuleType:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not all(callable(getattr(backend, method, None)) for method in METHODS):
        raise SkipTest("Rust extension does not support recent-conversation replay")
    return backend


class RecentConversationReplayRustTest(ZulipTestCase):
    def apply(self, state: dict[str, Any], event: dict[str, Any]) -> None:
        apply_events(
            self.example_user("hamlet"),
            state=state,
            events=[event],
            fetch_event_types=None,
            client_gravatar=False,
            slim_presence=True,
            include_subscribers=False,
            linkifier_url_template=False,
            user_list_incomplete=False,
            include_deactivated_groups=False,
        )

    def message(self, message_id: int, participants: list[int]) -> dict[str, Any]:
        return {
            "type": "message",
            "message": {
                "id": message_id,
                "type": "private",
                "display_recipient": [{"id": user_id} for user_id in participants],
            },
            "flags": ["read"],
        }

    def pin(self, participants: list[int], *, pinned: bool) -> dict[str, Any]:
        return {"type": "direct_message_conversation", "user_ids": participants, "pinned": pinned}

    def test_assemble_and_finalize_duplicate_groups_and_pinned_only_conversation(self) -> None:
        backend = extension()
        if not all(
            hasattr(backend, name)
            for name in ("assemble_recent_conversations", "finalize_recent_conversations")
        ):
            self.skipTest("Rust extension does not support recent-conversation formatting")
        recent = {101: 1000, 102: 1100, 103: 1100, 104: 7}
        subscriptions = [
            (101, 800, False),
            (101, 700, False),
            (101, 700, True),
            (102, 799, False),
            (103, 700, False),
            (103, 800, False),
            (104, 1, True),
            (104, 1, False),
        ]
        with patch.object(native_recent_conversations, "_backend", backend):
            raw = native_recent_conversations.assemble(1, recent, subscriptions)
            assert raw is not None
            self.assertEqual(list(raw), [frozenset({700, 800}), frozenset({799}), frozenset()])
            self.assertEqual(
                native_recent_conversations.finalize(raw),
                [
                    {"user_ids": [700, 800], "max_message_id": 1100, "pinned": False},
                    {"user_ids": [799], "max_message_id": 1100, "pinned": False},
                    {"user_ids": [], "max_message_id": 7, "pinned": True},
                ],
            )
            malformed: dict[frozenset[int], RecentPrivateConversationDict] = {
                frozenset({2**100}): {"max_message_id": 1, "pinned": False}
            }
            self.assertIsNone(native_recent_conversations.finalize(malformed))
            self.assertIsNone(native_recent_conversations.assemble(1, {101: 2**100}, subscriptions))

    def test_register_assembly_and_replay_preserve_both_real_routes(self) -> None:
        backend = extension()
        if not all(
            hasattr(backend, method)
            for method in ("assemble_recent_conversations", "finalize_recent_conversations")
        ):
            self.skipTest("Rust extension does not support recent-conversation formatting")
        assembly_enabled = environ.get("ZULIP_RUST_RECENT_CONVERSATIONS") == "1"
        replay_enabled = environ.get("ZULIP_RUST_RECENT_CONVERSATION_REPLAY") == "1"
        self.assertEqual(native_recent_conversations.assembly_available(), assembly_enabled)

        owner = self.example_user("hamlet")
        peer = self.example_user("cordelia")
        other = self.example_user("othello")
        direct = self.send_personal_message(owner, peer)
        self_message = self.send_personal_message(owner, owner)
        group = self.send_group_direct_message(owner, [peer, other])
        group_members = sorted([peer.id, other.id])
        queued_latest = group + 100
        events = [
            {**self.message(queued_latest, [owner.id, other.id, peer.id]), "id": 7},
            {**self.pin(group_members, pinned=True), "id": 8},
        ]
        params = {
            "event_types": orjson.dumps(
                ["recent_private_conversations", "message", "direct_message_conversation"]
            ).decode()
        }
        queue = EventQueueData(queue_id="15:21", idle_queue_timeout_secs=600)
        self.login_user(owner)

        def request(session: bool) -> tuple[int, bytes]:
            with stub_event_queue_user_events(queue, events):
                response = (
                    self.client_post("/json/register", params)
                    if session
                    else self.api_post(owner, "/api/v1/register", params)
                )
            self.assert_json_success(response)
            return response.status_code, response.content

        def decline(
            me: int, recent: dict[int, int], subscriptions: list[tuple[int, int, bool]]
        ) -> None:
            # The bridge rejects the entire noncanonical copy. Django keeps
            # its original selected rows for the Python fallback.
            malformed = [*subscriptions, (next(iter(recent)), True, False)]
            self.assertIsNone(backend.assemble_recent_conversations(me, recent, malformed))

        def decline_finalization(
            conversations: dict[frozenset[int], RecentPrivateConversationDict],
        ) -> None:
            malformed = {
                **conversations,
                frozenset({2**100}): {"max_message_id": 1, "pinned": False},
            }
            self.assertIsNone(backend.finalize_recent_conversations(malformed))

        for session in (False, True):
            with self.subTest(session=session):
                with (
                    patch.object(native_recent_conversations, "_backend", None),
                    patch.object(native_recent_conversation_replay, "_backend", None),
                ):
                    expected = request(session)
                data = orjson.loads(expected[1])
                self.assertEqual(data["last_event_id"], 8)
                self.assertEqual(
                    data["recent_private_conversations"],
                    [
                        {
                            "user_ids": group_members,
                            "max_message_id": queued_latest,
                            "pinned": True,
                        },
                        {"user_ids": [], "max_message_id": self_message, "pinned": False},
                        {"user_ids": [peer.id], "max_message_id": direct, "pinned": False},
                    ],
                )
                with (
                    patch.object(
                        backend,
                        "assemble_recent_conversations",
                        wraps=backend.assemble_recent_conversations,
                    ) as assembly,
                    patch.object(
                        backend,
                        "finalize_recent_conversations",
                        wraps=backend.finalize_recent_conversations,
                    ) as finalization,
                    patch.object(
                        backend, METHODS[0], wraps=getattr(backend, METHODS[0])
                    ) as message,
                    patch.object(backend, METHODS[1], wraps=getattr(backend, METHODS[1])) as pin,
                ):
                    self.assertEqual(request(session), expected)
                self.assertEqual(assembly.call_count, int(assembly_enabled))
                self.assertEqual(finalization.call_count, int(assembly_enabled))
                self.assertEqual(message.call_count, int(replay_enabled))
                self.assertEqual(pin.call_count, int(replay_enabled))

                reject_assembly = Mock(side_effect=decline)
                reject_finalization = Mock(side_effect=decline_finalization)
                for candidate in (
                    SimpleNamespace(),
                    SimpleNamespace(
                        assemble_recent_conversations=reject_assembly,
                        finalize_recent_conversations=backend.finalize_recent_conversations,
                    ),
                    SimpleNamespace(
                        assemble_recent_conversations=backend.assemble_recent_conversations,
                        finalize_recent_conversations=reject_finalization,
                    ),
                ):
                    with patch.object(native_recent_conversations, "_backend", candidate):
                        self.assertEqual(request(session), expected)
                reject_assembly.assert_called_once()
                reject_finalization.assert_called_once()

    def test_register_order_and_pin_identity(self) -> None:
        backend = extension()
        owner = self.example_user("hamlet").id
        direct = frozenset({610})
        group = frozenset({610, 611})
        old = {"max_message_id": 18, "pinned": True}
        group_row = {"max_message_id": 12, "pinned": False}
        conversations = {direct: old, group: group_row}
        state = {"max_message_id": 99, "raw_recent_private_conversations": conversations}
        with patch.object(native_recent_conversation_replay, "_backend", backend):
            self.apply(state, self.message(12, [610, owner, 610, owner]))
            self.apply(state, self.message(13, [owner, owner]))
            self.apply(state, self.pin([611, 610, 611], pinned=True))
            self.apply(state, self.pin([610, owner], pinned=False))
        self.assertEqual(list(conversations), [direct, group, frozenset()])
        self.assertIs(state["raw_recent_private_conversations"], conversations)
        self.assertIsNot(conversations[direct], old)
        self.assertIs(conversations[group], group_row)
        self.assertEqual(conversations[direct], {"max_message_id": 12, "pinned": True})
        self.assertEqual(conversations[group], {"max_message_id": 12, "pinned": True})
        self.assertEqual(conversations[frozenset()], {"max_message_id": 13, "pinned": False})
        self.assertEqual(state["max_message_id"], 99)

    def test_replay_matches_the_original_at_each_step(self) -> None:
        backend = extension()
        owner = self.example_user("hamlet").id
        generator = Random(7937)
        events: list[dict[str, Any]] = []
        for index in range(480):
            participants = [
                generator.choice([601, 602, 603, 604, 605]) for _ in range(generator.randrange(7))
            ]
            if index % 3:
                people = [owner, *participants]
                generator.shuffle(people)
                events.append(self.message(generator.randrange(250), people))
            else:
                events.append(self.pin(participants, pinned=bool(index % 2)))

        def timeline(candidate: object) -> list[tuple[object, object]]:
            state: dict[str, Any] = {"max_message_id": 0, "raw_recent_private_conversations": {}}
            changes: list[tuple[object, object]] = []
            with patch.object(native_recent_conversation_replay, "_backend", candidate):
                for event in events:
                    self.apply(state, event)
                    rows = state["raw_recent_private_conversations"]
                    changes.append(
                        (
                            state["max_message_id"],
                            [(ids, list(row.items())) for ids, row in rows.items()],
                        )
                    )
            return changes

        accepted: list[bool] = []

        def witness(method: str, *args: Any) -> bool:
            result: bool = getattr(backend, method)(*args)
            accepted.append(result)
            return result

        candidate = SimpleNamespace(
            replay_recent_conversation_message=lambda *args: witness(METHODS[0], *args),
            replay_recent_conversation_pin=lambda *args: witness(METHODS[1], *args),
        )
        reference = timeline(None)
        self.assertEqual(timeline(candidate), reference)
        self.assertEqual(accepted, [True] * len(events))
        self.assertEqual(timeline(SimpleNamespace()), reference)
        decline = Mock(return_value=False)
        self.assertEqual(timeline(SimpleNamespace(**dict.fromkeys(METHODS, decline))), reference)
        self.assertEqual(decline.call_count, len(events))

    def test_unusual_values_decline_before_mutation_and_use_python(self) -> None:
        backend = extension()
        owner = self.example_user("hamlet").id
        other = 2**100
        conversation = frozenset({other})
        row = {"max_message_id": 11, "pinned": False}
        conversations = {conversation: row}
        state = {"max_message_id": 11, "raw_recent_private_conversations": conversations}

        self.assertFalse(
            backend.replay_recent_conversation_message(conversations, owner, 12, [{"id": other}])
        )
        self.assertFalse(backend.replay_recent_conversation_pin(conversations, [other], True))
        self.assertIs(conversations[conversation], row)
        self.assertEqual(row, {"max_message_id": 11, "pinned": False})

        with patch.object(native_recent_conversation_replay, "_backend", backend):
            self.apply(state, self.message(12, [owner, other]))
            self.apply(state, self.pin([other], pinned=True))
        self.assertEqual(conversations[conversation], {"max_message_id": 12, "pinned": True})
