from collections.abc import Callable, Iterable
from importlib import import_module
from random import Random
from types import SimpleNamespace
from typing import Any
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson

from zerver.lib import native_schedule_replay
from zerver.lib.events import apply_events
from zerver.lib.test_classes import ZulipTestCase


def extension() -> Callable[[object, object], bool]:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    apply = getattr(backend, "apply_scheduled_list_event", None)
    if not callable(apply):
        raise SkipTest("Rust extension does not support scheduled-list replay")
    return apply


class ScheduleReplayRustTest(ZulipTestCase):
    def replay(
        self,
        state: dict[str, Any],
        events: Iterable[dict[str, Any]],
        *,
        fetch: set[str] | None = None,
    ) -> None:
        apply_events(
            self.example_user("hamlet"),
            state=state,
            events=events,
            fetch_event_types=fetch,
            client_gravatar=False,
            slim_presence=True,
            include_subscribers=False,
            linkifier_url_template=False,
            user_list_incomplete=False,
            include_deactivated_groups=False,
        )

    def test_ordered_replay_and_row_identity(self) -> None:
        native = extension()
        first = {"scheduled_message_id": 1, "scheduled_delivery_timestamp": 10}
        tied = {"scheduled_message_id": 2, "scheduled_delivery_timestamp": 20}
        addition = {"scheduled_message_id": 3, "scheduled_delivery_timestamp": 20}
        edit = {**tied, "content": "Edited without rescheduling"}
        earlier = {**addition, "scheduled_delivery_timestamp": 5}
        one_reminder = {"reminder_id": 6, "scheduled_delivery_timestamp": 10}
        next_reminder = {"reminder_id": 7, "scheduled_delivery_timestamp": 10}
        messages = [first, tied]
        reminders = [one_reminder]
        state = {"scheduled_messages": messages, "reminders": reminders}
        add_message = {"type": "scheduled_messages", "op": "add", "scheduled_messages": [addition]}
        edit_message = {"type": "scheduled_messages", "op": "update", "scheduled_message": edit}
        reschedule = {"type": "scheduled_messages", "op": "update", "scheduled_message": earlier}
        remove_missing = {"type": "scheduled_messages", "op": "remove", "scheduled_message_id": 99}
        add_reminder = {"type": "reminders", "op": "add", "reminders": [next_reminder]}
        remove_reminder = {"type": "reminders", "op": "remove", "reminder_id": 6}
        recorded: list[bool] = []

        def witness(selected: object, event: object) -> bool:
            used = native(selected, event)
            recorded.append(used)
            return used

        with patch.object(
            native_schedule_replay, "_backend", SimpleNamespace(apply_scheduled_list_event=witness)
        ):
            self.replay(state, [add_message, edit_message, add_reminder])
            self.assertEqual(messages, [first, edit, addition])
            self.assertEqual(reminders, [one_reminder, next_reminder])
            self.assertIs(messages[1], edit)
            self.assertIs(messages[2], addition)
            self.assertIs(reminders[0], one_reminder)
            self.replay(state, [reschedule, remove_missing, remove_reminder])
            self.assertEqual(messages, [earlier, first, edit])
            self.assertEqual(reminders, [next_reminder])
            self.assertIs(messages[0], earlier)
            self.assertIs(reminders[0], next_reminder)
            self.replay(state, [add_reminder], fetch={"scheduled_messages"})
        self.assertIs(state["scheduled_messages"], messages)
        self.assertIs(state["reminders"], reminders)
        self.assertEqual(recorded, [True] * 6)

    def test_differential_real_reducer_and_older_extension(self) -> None:
        native = extension()
        generator = Random(3857)
        events: list[dict[str, Any]] = []
        next_id = 0
        for family, key in (
            ("scheduled_messages", "scheduled_message_id"),
            ("reminders", "reminder_id"),
        ):
            known: list[int] = []
            for _ in range(240):
                operation = generator.choice(
                    ["add", "remove"] if key == "reminder_id" else ["add", "remove", "update"]
                )
                if not known or operation == "add":
                    card = {
                        key: next_id,
                        "scheduled_delivery_timestamp": generator.randrange(-4, 5),
                    }
                    events.append({"type": family, "op": "add", family: [card]})
                    known.append(next_id)
                    next_id += 1
                elif operation == "update":
                    card = {
                        key: generator.choice(known),
                        "scheduled_delivery_timestamp": generator.randrange(-4, 5),
                    }
                    events.append({"type": family, "op": "update", "scheduled_message": card})
                else:
                    identifier = generator.choice([*known, -1])
                    events.append({"type": family, "op": "remove", key: identifier})
                    if identifier != -1:
                        known.remove(identifier)

        def replay_all(backend: object) -> list[bytes]:
            timeline = []
            state: dict[str, Any] = {"scheduled_messages": [], "reminders": []}
            with patch.object(native_schedule_replay, "_backend", backend):
                for event in events:
                    self.replay(state, [event])
                    timeline.append(orjson.dumps(state))
            return timeline

        expected = replay_all(None)
        accepted: list[bool] = []

        def witness(selected: object, event: object) -> bool:
            claimed = native(selected, event)
            accepted.append(claimed)
            return claimed

        self.assertEqual(replay_all(SimpleNamespace(apply_scheduled_list_event=witness)), expected)
        self.assertEqual(accepted, [True] * len(events))
        self.assertEqual(replay_all(SimpleNamespace()), expected)
        decline = Mock(return_value=False)
        self.assertEqual(replay_all(SimpleNamespace(apply_scheduled_list_event=decline)), expected)
        self.assertEqual(decline.call_count, len(events))

    def test_native_declines_without_mutating_then_python_preserves_legacy(self) -> None:
        native = extension()
        first = {"scheduled_message_id": 6, "scheduled_delivery_timestamp": 1}
        duplicate = {"scheduled_message_id": 6, "scheduled_delivery_timestamp": 2}
        tail = {"scheduled_message_id": 7, "scheduled_delivery_timestamp": 3}
        original = [first, duplicate, tail]
        state = {"scheduled_messages": original}
        removal = {"type": "scheduled_messages", "op": "remove", "scheduled_message_id": 6}
        legacy_time = {
            "type": "scheduled_messages",
            "op": "add",
            "scheduled_messages": [
                {"scheduled_message_id": 8, "scheduled_delivery_timestamp": 1.5}
            ],
        }
        malformed = {"type": "scheduled_messages", "op": "add", "scheduled_messages": []}
        for event in (removal, legacy_time, malformed):
            self.assertFalse(native(original, event))
            self.assertEqual(original, [first, duplicate, tail])
        with patch.object(
            native_schedule_replay, "_backend", SimpleNamespace(apply_scheduled_list_event=native)
        ):
            self.replay(state, [removal, legacy_time])
            with self.assertRaises(AssertionError):
                self.replay(state, [malformed])
        self.assertIs(state["scheduled_messages"], original)
        self.assertEqual(original, [legacy_time["scheduled_messages"][0], duplicate, tail])
        self.assertIs(original[1], duplicate)
