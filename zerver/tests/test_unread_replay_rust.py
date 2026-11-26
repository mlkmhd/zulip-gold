from collections.abc import Iterator
from importlib import import_module
from os import environ
from random import Random
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import SkipTest
from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import message, native_unread_replay
from zerver.lib.events import apply_events
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.topic import TOPIC_NAME
from zerver.models import UserProfile, UserTopic

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
    def test_private_unread_arrival_plan_preserves_display_roster(self) -> None:
        backend = extension()
        native = getattr(backend, "plan_private_unread_arrival", None)
        if not callable(native):
            self.skipTest("Rust extension does not support private unread arrival planning")
        self.assertEqual(native(999, []), (999, None))
        self.assertEqual(native(999, [999, 999]), (999, None))
        self.assertEqual(native(999, [999, 18, 999]), (18, None))
        self.assertEqual(native(999, [18, 999, 18]), (None, "18,18,999"))

        rng = Random(712)
        ids = [-(2**63), 2**63 - 1, -35, 16, 18, 999]
        for _ in range(500):
            roster = rng.choices(ids, k=rng.randrange(25))
            original = roster.copy()
            others = [identifier for identifier in roster if identifier != 999]
            expected = (
                (others[0] if others else 999, None)
                if len(others) <= 1
                else (None, ",".join(str(identifier) for identifier in sorted(roster)))
            )
            self.assertEqual(native(999, roster), expected)
            self.assertEqual(roster, original)

        for malformed in ([1, True], [1, 2**100], (1, 2), [1, "2"]):
            self.assertIsNone(native(999, malformed))
        self.assertIsNone(native(True, [2]))
        self.assertIsNone(native(2**100, [2]))

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
                    move[TOPIC_NAME] = "بحث" if index % 2 else ""
                events.append(move)

        def timeline(candidate: object) -> list[str]:
            raw = empty_unread()
            history = []
            with (
                patch.object(native_unread_replay, "_backend", candidate),
                patch.object(native_unread_replay, "_remove_backend", None),
                patch.object(native_unread_replay, "_move_backend", None),
            ):
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

    def test_default_unread_removal_receives_only_selected_state(self) -> None:
        raw = empty_unread()
        raw["pm_dict"][7] = {"other_user_id": 2}
        raw["stream_dict"][9] = {"stream_id": 4, "topic": "Keep"}
        raw["mentions"].update({7, 9})
        event = {"type": "update_message_flags", "flag": "read", "op": "add", "messages": [7]}
        backend = native_unread_replay._remove_backend
        if backend is not None:
            with patch.object(
                backend, "remove_unread_messages", wraps=backend.remove_unread_messages
            ) as witness:
                self.replay(raw, event)
            witness.assert_called_once_with(raw, [7])
            self.assertIs(witness.call_args.args[0], raw)
        else:
            self.replay(raw, event)
        self.assertEqual(raw["pm_dict"], {})
        self.assertEqual(raw["stream_dict"], {9: {"stream_id": 4, "topic": "Keep"}})
        self.assertEqual(raw["mentions"], {9})

    def test_default_unread_move_preserves_row_and_unrelated_flags(self) -> None:
        raw = empty_unread()
        moved = {"stream_id": 4, "topic": "Old"}
        raw["stream_dict"][9] = moved
        raw["mentions"].add(9)
        raw["unmuted_stream_msgs"].add(9)
        raw["pm_dict"][10] = {"other_user_id": 5}
        event = {
            "type": "update_message",
            "message_ids": [10, 9, 9],
            "new_stream_id": 6,
            TOPIC_NAME: "",
        }
        content_only = {"type": "update_message", "message_ids": [9]}
        backend = native_unread_replay._move_backend
        if backend is not None:
            with patch.object(
                backend, "move_unread_messages", wraps=backend.move_unread_messages
            ) as witness:
                self.replay(raw, content_only)
                self.replay(raw, event)
            witness.assert_called_once_with(raw, [10, 9, 9], 6, "")
        else:
            self.replay(raw, content_only)
            self.replay(raw, event)
        self.assertIs(raw["stream_dict"][9], moved)
        self.assertEqual(moved, {"stream_id": 6, "topic": ""})
        self.assertEqual(raw["pm_dict"], {10: {"other_user_id": 5}})
        self.assertEqual(raw["mentions"], {9})
        self.assertEqual(raw["unmuted_stream_msgs"], {9})

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
                    TOPIC_NAME: "Moved",
                },
            )
        self.assertEqual(set(raw["pm_dict"]), {8, 9})
        self.assertIs(raw["stream_dict"][2], row)
        self.assertEqual(row, {"stream_id": 11, "topic": "Moved"})
        self.assertEqual(raw["mentions"], {2})
        self.assertEqual(raw["muted_stream_ids"], {2})
        self.assertTrue(raw["old_unreads_missing"])


class PrivateUnreadArrivalDjangoDispatchRustTest(SimpleTestCase):
    @staticmethod
    def replay(raw: dict[str, Any], msg: dict[str, Any], flags: list[str]) -> None:
        apply_events(
            UserProfile(id=999),
            state={"raw_unread_msgs": raw, "max_message_id": 0},
            events=[{"type": "message", "message": msg, "flags": flags}],
            fetch_event_types=None,
            client_gravatar=False,
            slim_presence=True,
            include_subscribers=False,
            linkifier_url_template=False,
            user_list_incomplete=False,
            include_deactivated_groups=False,
        )

    @staticmethod
    def private(message_id: int, display_recipient: object) -> dict[str, Any]:
        return {"type": "private", "id": message_id, "display_recipient": display_recipient}

    def test_real_arrival_only_passes_ids_and_preserves_state_and_aliases(self) -> None:
        backend = extension()
        native = getattr(backend, "plan_private_unread_arrival", None)
        if not callable(native):
            self.skipTest("Rust extension does not support private unread arrival planning")
        enabled = environ.get("ZULIP_RUST_PRIVATE_UNREAD_ARRIVAL") == "1"
        self.assertIs(native_unread_replay._private_arrival_backend, backend if enabled else None)
        raw = empty_unread()
        pm = {"other_user_id": 300}
        group = {"user_ids_string": "1,2,3"}
        raw["pm_dict"][70] = pm
        raw["huddle_dict"][71] = group
        raw["mentions"].add(72)
        raw["unmuted_stream_msgs"].add(12)
        containers = raw.copy()
        ourselves = {"id": 999, "full_name": "synthetic self"}
        other = {"id": 18, "full_name": "synthetic peer"}
        roster = [other, ourselves, other]
        incoming = self.private(12, roster)

        with (
            patch.object(backend, "plan_private_unread_arrival", wraps=native) as plan,
            patch.object(
                message,
                "get_topic_visibility_policy",
                return_value=UserTopic.VisibilityPolicy.MUTED,
            ) as visibility,
        ):
            self.replay(raw, self.private(10, []), [])
            self.replay(raw, self.private(11, [ourselves, {"id": 0}, ourselves]), ["mentioned"])
            self.replay(raw, incoming, ["topic_wildcard_mentioned"])
            self.replay(raw, self.private(13, [{"id": 22}]), ["read", "mentioned"])
            self.replay(
                raw,
                {"type": "stream", "id": 14, "stream_id": 12, TOPIC_NAME: "synthetic"},
                ["stream_wildcard_mentioned"],
            )
            self.replay(raw, self.private(15, [{"id": 8}, {"id": 3}]), [])

        visibility.assert_called_once()
        self.assertEqual(
            list(raw["pm_dict"].items()),
            [(70, pm), (10, {"other_user_id": 999}), (11, {"other_user_id": 0})],
        )
        self.assertEqual(
            list(raw["huddle_dict"].items()),
            [(71, group), (12, {"user_ids_string": "18,18,999"}), (15, {"user_ids_string": "3,8"})],
        )
        self.assertEqual(raw["stream_dict"], {14: {"stream_id": 12, "topic": "synthetic"}})
        self.assertEqual(raw["mentions"], {11, 12, 72})
        self.assertEqual(raw["unmuted_stream_msgs"], {12})
        self.assertEqual(raw["muted_stream_ids"], {2})
        self.assertIs(raw["pm_dict"][70], pm)
        self.assertIs(raw["huddle_dict"][71], group)
        for key, container in containers.items():
            self.assertIs(raw[key], container)
        self.assertIs(incoming["display_recipient"], roster)
        self.assertIs(roster[0], roster[2])
        self.assertEqual([entry["id"] for entry in roster], [18, 999, 18])
        if enabled:
            self.assertEqual(
                plan.call_args_list,
                [
                    call(999, []),
                    call(999, [999, 0, 999]),
                    call(999, [18, 999, 18]),
                    call(999, [8, 3]),
                ],
            )
        else:
            plan.assert_not_called()

    def test_old_declining_and_invalid_planners_keep_python_projection(self) -> None:
        incoming = self.private(19, [{"id": 21}, {"id": 21}, {"id": 999}])
        for result in (None, (None, None), (21, "unexpected"), [21, None]):
            plan = Mock(return_value=result)
            for backend in (
                None,
                SimpleNamespace(),
                SimpleNamespace(plan_private_unread_arrival=plan),
            ):
                raw = empty_unread()
                with patch.object(native_unread_replay, "_private_arrival_backend", backend):
                    self.replay(raw, incoming, ["mentioned"])
                self.assertEqual(raw["huddle_dict"], {19: {"user_ids_string": "21,21,999"}})
                self.assertEqual(raw["mentions"], {19})
            plan.assert_called_once_with(999, [21, 21, 999])

    def test_noncanonical_containers_do_not_run_speculative_lookups(self) -> None:
        visits: list[str] = []

        class DisplayRecord(dict[str, int]):
            @override
            def __getitem__(self, key: str, /) -> int:
                visits.append(key)
                return super().__getitem__(key)

        class DisplayRoster(list[dict[str, int]]):
            @override
            def __iter__(self) -> Iterator[dict[str, int]]:
                visits.append("iteration")
                return super().__iter__()

        plan = Mock()
        with patch.object(
            native_unread_replay,
            "_private_arrival_backend",
            SimpleNamespace(plan_private_unread_arrival=plan),
        ):
            raw = empty_unread()
            self.replay(raw, self.private(20, [DisplayRecord(id=4), {"id": 999}]), [])
            self.assertEqual(visits, ["id", "id"])
            visits.clear()
            self.replay(raw, self.private(21, DisplayRoster([{"id": 4}, {"id": 5}])), [])
            self.assertEqual(visits, ["iteration", "iteration"])
            self.replay(raw, self.private(22, [{"id": True}, {"id": 1}]), [])
            self.assertEqual(raw["huddle_dict"][22], {"user_ids_string": "True,1"})
            with self.assertRaisesRegex(KeyError, "^'id'$"):
                self.replay(raw, self.private(23, [{"full_name": "missing numeric ID"}]), [])
        plan.assert_not_called()

    def test_native_integer_range_decline_keeps_python_precision_and_errors(self) -> None:
        backend = extension()
        with patch.object(native_unread_replay, "_private_arrival_backend", backend):
            raw = empty_unread()
            huge = 2**100
            self.replay(raw, self.private(24, [{"id": huge}, {"id": 5}]), [])
            self.assertEqual(raw["huddle_dict"], {24: {"user_ids_string": f"5,{huge}"}})
            with self.assertRaises(TypeError):
                self.replay(raw, self.private(25, [{"id": 1}, {"id": "2"}]), [])
            self.assertNotIn(25, raw["huddle_dict"])
