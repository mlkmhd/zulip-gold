from importlib import import_module
from random import Random
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import SkipTest
from unittest.mock import Mock, patch

from zerver.lib import native_unread_replay
from zerver.lib.events import apply_events
from zerver.lib.test_classes import ZulipTestCase

METHODS = ("remove_unread_messages", "restore_unread_messages", "move_unread_messages")


def extension() -> ModuleType:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not all(callable(getattr(backend, method, None)) for method in METHODS):
        raise SkipTest("Rust extension does not support unread-state replay")
    return backend


def empty_unread() -> dict[str, Any]:
    return {
        "pm_dict": {},
        "stream_dict": {},
        "huddle_dict": {},
        "mentions": set(),
        "unmuted_stream_msgs": set(),
        "muted_stream_ids": {2},
        "old_unreads_missing": True,
    }


class UnreadReplayRustTest(ZulipTestCase):
    def replay(self, raw: dict[str, Any], event: dict[str, Any]) -> None:
        apply_events(
            self.example_user("hamlet"),
            state={"raw_unread_msgs": raw},
            events=[event],
            fetch_event_types=None,
            client_gravatar=False,
            slim_presence=True,
            include_subscribers=False,
            linkifier_url_template=False,
            user_list_incomplete=False,
            include_deactivated_groups=False,
        )

    def test_batch_replay_matches_python_after_each_event(self) -> None:
        backend = extension()
        generator = Random(7819)
        own_id = self.example_user("hamlet").id
        events: list[dict[str, Any]] = []
        for index in range(450):
            ids = [generator.randrange(1, 45) for _ in range(generator.randrange(5))]
            op = generator.choice(("read", "unread", "move"))
            if op == "read":
                events.append(
                    {"type": "update_message_flags", "flag": "read", "op": "add", "messages": ids}
                )
            elif op == "unread":
                details: dict[str, Any] = {}
                for message_id in ids:
                    if generator.randrange(2):
                        people = [
                            generator.choice([own_id, 91, 403, 1154])
                            for _ in range(generator.randrange(5))
                        ]
                        details[str(message_id)] = {"type": "private", "user_ids": people}
                    else:
                        details[str(message_id)] = {
                            "type": "stream",
                            "stream_id": 1 + index % 7,
                            "topic": "موضوع" if index % 2 else "",
                            "unmuted_stream_msg": bool(index % 3),
                        }
                    if index % 5:
                        details[str(message_id)]["mentioned"] = index % 2 == 0
                events.append(
                    {
                        "type": "update_message_flags",
                        "flag": "read",
                        "op": "remove",
                        "message_details": details,
                    }
                )
            else:
                move: dict[str, Any] = {"type": "update_message", "message_ids": ids}
                if index % 3 != 0:
                    move["new_stream_id"] = 1 + index % 9
                if index % 3 != 1:
                    move["subject"] = "بحث" if index % 2 else ""
                events.append(move)

        def timeline(candidate: object) -> list[str]:
            raw = empty_unread()
            history = []
            with patch.object(native_unread_replay, "_backend", candidate):
                for event in events:
                    self.replay(raw, event)
                    # The dict/set representation also records iteration order.
                    history.append(repr(raw))
            return history

        results: list[bool] = []

        def witness(method: str, *args: Any) -> bool:
            accepted: bool = getattr(backend, method)(*args)
            results.append(accepted)
            return accepted

        native = SimpleNamespace(
            remove_unread_messages=lambda *args: witness("remove_unread_messages", *args),
            restore_unread_messages=lambda *args: witness("restore_unread_messages", *args),
            move_unread_messages=lambda *args: witness("move_unread_messages", *args),
        )
        reference = timeline(None)
        self.assertEqual(timeline(native), reference)
        self.assertEqual(results, [True] * len(events))
        self.assertEqual(timeline(SimpleNamespace()), reference)
        decline = Mock(return_value=False)
        self.assertEqual(timeline(SimpleNamespace(**dict.fromkeys(METHODS, decline))), reference)
        self.assertEqual(decline.call_count, len(events))

    def test_decline_leaves_the_selected_state_untouched(self) -> None:
        backend = extension()
        raw = empty_unread()
        row = {"stream_id": 10, "topic": "Original"}
        raw["stream_dict"].update({2: row, 3: row.copy()})
        raw["mentions"].add(2)
        before = repr(raw)
        self.assertFalse(backend.remove_unread_messages(raw, [2, 2**100]))
        details = {
            "8": {"type": "private", "user_ids": []},
            " 9 ": {"type": "private", "user_ids": [99]},
        }
        self.assertFalse(backend.restore_unread_messages(raw, 5, details))
        self.assertFalse(backend.move_unread_messages(raw, [2, 3], 11, 12))
        self.assertEqual(repr(raw), before)
        with patch.object(native_unread_replay, "_backend", backend):
            self.replay(
                raw,
                {
                    "type": "update_message_flags",
                    "flag": "read",
                    "op": "remove",
                    "message_details": details,
                },
            )
            self.replay(
                raw,
                {
                    "type": "update_message",
                    "message_ids": [2],
                    "new_stream_id": 11,
                    "subject": "Moved",
                },
            )
        self.assertEqual(set(raw["pm_dict"]), {8, 9})
        self.assertIs(raw["stream_dict"][2], row)
        self.assertEqual(row, {"stream_id": 11, "topic": "Moved"})
        self.assertEqual(raw["mentions"], {2})
        self.assertEqual(raw["muted_stream_ids"], {2})
        self.assertTrue(raw["old_unreads_missing"])
