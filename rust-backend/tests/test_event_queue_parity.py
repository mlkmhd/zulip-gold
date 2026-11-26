# ruff: noqa: INP001
"""Compare in-process queue semantics with the checked-out Python code."""

import ast
import json
import os
import random
import unittest
from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from importlib import util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load_bridge(*, enabled: bool) -> ModuleType:
    path = ROOT / "zerver/lib/native_event_queue.py"
    spec = util.spec_from_file_location("native_event_queue", path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    with patch.dict(
        os.environ,
        {
            "ZULIP_RUST_EVENT_QUEUE": str(int(enabled)),
            "ZULIP_RUST_PREPARED_EVENT_CLASSIFICATION": "0",
        },
    ):
        spec.loader.exec_module(module)
    return module


def load_queue(source: Path, bridge: ModuleType) -> ModuleType:
    module = ModuleType("queue_formatters")
    vars(module).update(
        deque=deque,
        Mapping=Mapping,
        Any=object,
        copy=SimpleNamespace(deepcopy=deepcopy),
        native_event_queue=bridge,
    )
    tree = ast.parse(source.read_text(), filename=str(source))
    queue_parts = {"EventQueue", "compute_full_event_type", "prune_internal_data"}
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in queue_parts
    ]
    exec(compile(ast.Module(nodes, []), str(source), "exec"), vars(module))  # noqa: S102
    return module


class EventQueueParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        current = ROOT / "zerver/tornado/event_queue.py"
        baseline = Path(os.environ.get("ZULIP_QUEUE_REFERENCE", current))
        cls.bridge = load_bridge(enabled=True)
        assert cls.bridge._backend is not None
        cls.native = load_queue(current, cls.bridge)
        cls.reference = load_queue(baseline, load_bridge(enabled=False))

    def test_seeded_queue_operations(self) -> None:
        rng = random.Random(51820)
        for _ in range(2000):
            rust = self.native.EventQueue("test")
            python = self.reference.EventQueue("test")
            for n in range(rng.randrange(1, 31)):
                kind = rng.choice(
                    ("update_message_flags", "update_message_flags", "message", "heartbeat")
                )
                event: dict[str, object] = {"type": kind}
                if kind == "update_message_flags":
                    event.update(
                        flag=rng.choice(("read", "starred", "collapsed")),
                        operation=rng.choice(("add", "remove")),
                        all=rng.randrange(8) == 0,
                        messages=rng.sample(range(100), rng.randrange(10)),
                    )
                    if rng.getrandbits(1):
                        event["timestamp"] = n
                if kind == "message":
                    event.update(message={"content": "Local sample"}, internal_data={"flag": True})
                self.assertEqual(
                    self.bridge.classify(event), self.reference.compute_full_event_type(event)
                )
                rust.push(deepcopy(event))
                python.push(deepcopy(event))
                if n % 7 == 4:
                    self.assertEqual(json.dumps(rust.to_dict()), json.dumps(python.to_dict()))
                    self.assertEqual(json.dumps(rust.contents()), json.dumps(python.contents()))
                    rust.prune(n - 3)
                    python.prune(n - 3)
            self.assertEqual(json.dumps(rust.contents(True)), json.dumps(python.contents(True)))
            self.assertEqual(json.dumps(rust.to_dict()), json.dumps(python.to_dict()))

    def test_restore_preserves_collisions_and_exact_event_instances(self) -> None:
        earlier = {"id": 2, "type": "heartbeat"}
        real = {"id": 4, "type": "message", "internal_data": {"flag": True}}
        virtual = {
            "old": {"id": 4, "type": "old"},
            "latest": {"id": 4, "type": "latest"},
            "early": {"id": 3, "type": "early"},
        }
        state = {
            "queue": [earlier, real],
            "virtual_events": virtual,
            "id": "restored",
            "next_event_id": 6,
            "newest_pruned_id": -1,
        }
        expected = self.reference.EventQueue.from_dict(state).contents(True)
        merged = self.bridge.merge(deque([earlier, real]), virtual)
        self.assertIsNotNone(merged)
        self.assertEqual(merged, expected)
        self.assertIs(merged[0], earlier)
        self.assertIs(merged[1], virtual["early"])
        self.assertIs(merged[2], real)
        self.assertIs(merged[3], virtual["latest"])
        public = self.native.EventQueue.from_dict(state).contents(False)
        self.assertNotIn("internal_data", public[2])
        self.assertIn("internal_data", real)

    def test_flag_folding_keeps_list_aliases_and_last_available_timestamp(self) -> None:
        ids = [41]
        previous = {"messages": ids, "timestamp": 2, "id": 0}
        self.assertTrue(self.bridge.fold_flag_event(previous, {"id": 3, "messages": [21, 25]}))
        self.assertEqual(previous, {"messages": [41, 21, 25], "timestamp": 2, "id": 3})
        self.assertIs(previous["messages"], ids)
        self.assertTrue(
            self.bridge.fold_flag_event(previous, {"id": 4, "messages": ids, "timestamp": 5})
        )
        self.assertEqual(previous, {"messages": [41, 21, 25, 41, 21, 25], "timestamp": 5, "id": 4})

    def test_old_extension_and_unusual_state_defer(self) -> None:
        event = {"type": "update_message_flags", "all": 1, "operation": "add", "flag": "read"}
        self.assertIsNone(self.bridge.classify(event))
        self.assertEqual(self.native.compute_full_event_type(event), "all_flags/read/add")
        wide = deque([{"id": 2**80, "type": "heartbeat"}])
        self.assertIsNone(self.bridge.merge(wide, {}))
        previous = {"messages": [3], "id": 0}
        self.assertFalse(self.bridge.fold_flag_event(previous, {"id": 1, "messages": (2,)}))
        self.assertEqual(previous, {"messages": [3], "id": 0})
        with patch.object(self.bridge, "_backend", SimpleNamespace()):
            self.assertIsNone(self.bridge.classify({"type": "heartbeat"}))
            self.assertFalse(self.bridge.fold_flag_event({}, {}))
            self.assertIsNone(self.bridge.merge(deque(), {}))
