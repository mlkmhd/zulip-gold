from importlib import import_module
from random import Random
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import SkipTest
from unittest.mock import Mock, patch

from zerver.lib import native_recent_conversation_replay
from zerver.lib.events import apply_events
from zerver.lib.test_classes import ZulipTestCase

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
