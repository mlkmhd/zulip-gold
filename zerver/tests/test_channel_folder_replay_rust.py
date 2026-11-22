from collections.abc import Callable
from importlib import import_module
from random import Random
from types import SimpleNamespace
from typing import Any
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson

from zerver.lib import native_channel_folder_replay
from zerver.lib.events import apply_events
from zerver.lib.test_classes import ZulipTestCase


def extension() -> Callable[[object, object], bool]:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    apply = getattr(backend, "apply_channel_folder_event", None)
    if not callable(apply):
        raise SkipTest("Rust extension does not support channel-folder replay")
    return apply


class ChannelFolderReplayRustTest(ZulipTestCase):
    def replay(
        self, state: dict[str, Any], event: dict[str, Any], *, fetch: set[str] | None = None
    ) -> None:
        apply_events(
            self.example_user("hamlet"),
            state=state,
            events=[{"type": "channel_folder", **event}],
            fetch_event_types=fetch,
            client_gravatar=False,
            slim_presence=True,
            include_subscribers=False,
            linkifier_url_template=False,
            user_list_incomplete=False,
            include_deactivated_groups=False,
        )

    def test_stable_ids_requested_order_and_existing_object_identities(self) -> None:
        native = extension()
        first = {"id": 2, "name": "بحث", "order": 9}
        second = {"id": 1, "name": "Second", "order": 2}
        duplicate = {"id": 2, "name": "Third", "order": 4}
        rows = [first, second]
        state = {"channel_folders": rows}
        witness = Mock(wraps=native)
        with patch.object(
            native_channel_folder_replay,
            "_backend",
            SimpleNamespace(apply_channel_folder_event=witness),
        ):
            self.replay(state, {"op": "add", "channel_folder": duplicate})
            self.assertEqual(rows, [second, first, duplicate])
            self.assertIs(rows[2], duplicate)
            self.replay(
                state,
                {"op": "update", "channel_folder_id": 2, "data": {"is_archived": True}},
            )
            self.assertEqual([row.get("is_archived") for row in rows], [None, True, True])
            self.replay(state, {"op": "reorder", "order": [1, 2, 1]})
            self.assertEqual(rows, [first, duplicate, second])
            self.assertEqual([row["order"] for row in rows], [1, 1, 2])
            self.replay(state, {"op": "reorder", "order": [1, 2]}, fetch={"drafts"})
        witness.assert_called()
        self.assertEqual(witness.call_count, 3)
        self.assertIs(state["channel_folders"], rows)
        self.assertIs(rows[0], first)
        self.assertIs(rows[1], duplicate)
        self.assertIs(rows[2], second)

    def test_replay_matches_python_after_each_selected_event(self) -> None:
        native = extension()
        generator = Random(7748)
        events: list[dict[str, Any]] = []
        identifiers: set[int] = set()
        for n in range(360):
            op = generator.choice(("add", "update", "reorder"))
            identifier = generator.randrange(16)
            if op == "add":
                events.append({"op": op, "channel_folder": {"id": identifier, "name": f"بحث {n}"}})
                identifiers.add(identifier)
            elif op == "update":
                events.append(
                    {
                        "op": op,
                        "channel_folder_id": identifier,
                        "data": {"name": f"Folder {n}", "is_archived": bool(n % 2)},
                    }
                )
            else:
                order = sorted(identifiers)
                generator.shuffle(order)
                events.append({"op": op, "order": order})

        def timeline(backend: object) -> list[bytes]:
            state: dict[str, Any] = {"channel_folders": []}
            history = []
            with patch.object(native_channel_folder_replay, "_backend", backend):
                for event in events:
                    self.replay(state, orjson.loads(orjson.dumps(event)))
                    history.append(orjson.dumps(state))
            return history

        reference = timeline(None)
        accepted: list[bool] = []

        def witness(selected: object, event: object) -> bool:
            claimed = native(selected, event)
            accepted.append(claimed)
            return claimed

        self.assertEqual(timeline(SimpleNamespace(apply_channel_folder_event=witness)), reference)
        self.assertEqual(accepted, [True] * len(events))
        self.assertEqual(timeline(SimpleNamespace()), reference)
        decline = Mock(return_value=False)
        self.assertEqual(timeline(SimpleNamespace(apply_channel_folder_event=decline)), reference)
        self.assertEqual(decline.call_count, len(events))

    def test_declines_before_mutation_and_preserves_reference_fallback(self) -> None:
        native = extension()
        first = {"id": 1, "order": 10}
        second = {"id": 2, "order": 20}
        rows = [first, second]
        incomplete = {"type": "channel_folder", "op": "reorder", "order": [1]}
        self.assertFalse(native(rows, incomplete))
        self.assertEqual([row["order"] for row in rows], [10, 20])
        patch_event = {
            "type": "channel_folder",
            "op": "update",
            "channel_folder_id": 1,
            "data": [("name", "Legacy")],
        }
        self.assertFalse(native(rows, patch_event))
        self.assertNotIn("name", first)
        oversized = {"id": 2**100}
        add = {"type": "channel_folder", "op": "add", "channel_folder": oversized}
        self.assertFalse(native(rows, add))
        self.assertEqual(rows, [first, second])
        with patch.object(
            native_channel_folder_replay,
            "_backend",
            SimpleNamespace(apply_channel_folder_event=native),
        ):
            self.replay({"channel_folders": rows}, patch_event)
            self.replay({"channel_folders": rows}, add)
        self.assertEqual(first, {"id": 1, "order": 10, "name": "Legacy"})
        self.assertIs(rows[2], oversized)
