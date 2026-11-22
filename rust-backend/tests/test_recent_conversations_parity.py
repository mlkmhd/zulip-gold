# ruff: noqa: INP001
"""Exercise Rust's recent direct message snapshots without services."""

import ast
import json
import os
import random
import unittest
from collections import defaultdict
from importlib import util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load_bridge(*, enabled: bool) -> ModuleType:
    path = ROOT / "zerver/lib/native_recent_conversations.py"
    spec = util.spec_from_file_location("native_recent_conversations", path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    with patch.dict(os.environ, {"ZULIP_RUST_RECENT_CONVERSATIONS": str(int(enabled))}):
        spec.loader.exec_module(module)
    return module


def load_reference() -> ModuleType:
    workspace = Path(os.environ.get("ZULIP_RECENT_REFERENCE_DIR", ROOT / "zerver/lib"))
    message = workspace / "message.py"
    events = workspace / "events.py"
    message_ast = ast.parse(message.read_text(), filename=str(message))
    events_ast = ast.parse(events.read_text(), filename=str(events))
    recent_fn = next(
        node
        for node in message_ast.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_recent_private_conversations"
    )
    # Compile only the Python-only row assembly, not Django's queries.
    positions = [
        (index, node)
        for index, node in enumerate(recent_fn.body)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    data_start = next(index for index, node in positions if node.target.id == "recipient_map")
    data_end = next(
        index
        for index in range(data_start, len(recent_fn.body))
        if isinstance(recent_fn.body[index], ast.For)
    )
    loop = recent_fn.body[data_end]
    assert isinstance(loop, ast.For)
    loop.iter = ast.copy_location(ast.Name(id="subscriptions", ctx=ast.Load()), loop.iter)
    recent_fn.name = "assemble"
    recent_fn.args.args = [
        ast.arg(arg=name)
        for name in ("user_profile", "recipient_to_max_message_id", "subscriptions")
    ]
    recent_fn.returns = None
    recent_fn.body = [*recent_fn.body[data_start : data_start + 2], loop, recent_fn.body[-1]]

    post_process_fn = next(
        node
        for node in events_ast.body
        if isinstance(node, ast.FunctionDef) and node.name == "post_process_state"
    )
    context = ModuleType("recent_conversations_reference")
    vars(context).update(
        Any=object,
        UserProfile=object,
        defaultdict=defaultdict,
        native_recent_conversations=load_bridge(enabled=False),
    )
    syntax = ast.fix_missing_locations(ast.Module([recent_fn, post_process_fn], []))
    exec(compile(syntax, str(message), "exec"), vars(context))  # noqa: S102
    return context


class RecentConversationParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = load_bridge(enabled=True)
        assert cls.bridge.assembly_available()
        cls.reference = load_reference()

    def compare(self, me: int, recent: dict[int, int], rows: list[tuple[int, int, bool]]) -> None:
        native = self.bridge.assemble(me, recent, rows)
        self.assertIsNotNone(native)
        expected = self.reference.assemble(SimpleNamespace(id=me), recent, rows)
        self.assertEqual(list(native.items()), list(expected.items()))
        snapshot = {"raw_recent_private_conversations": expected}
        self.reference.post_process_state(None, snapshot, False, True)
        final = self.bridge.finalize(native)
        self.assertIsNotNone(final)
        self.assertEqual(json.dumps(final), json.dumps(snapshot["recent_private_conversations"]))

    def test_pin_self_and_equal_recency_order(self) -> None:
        recent = {55: 200, 61: 200, 31: 100, 63: 200, 86: -(2**63)}
        rows = [
            (55, 2, True),
            (61, 4, True),
            (61, 1, False),
            (61, 2, False),
            (31, 8, False),
            (31, 2, False),
            (63, 4, False),
            (63, 1, True),
            (63, 2, True),
            (86, 19, False),
        ]
        self.compare(2, recent, rows)
        state = self.bridge.assemble(2, recent, rows)
        rendered = self.bridge.finalize(state)
        self.assertEqual(
            rendered,
            [
                {"user_ids": [], "max_message_id": 200, "pinned": True},
                {"user_ids": [1, 4], "max_message_id": 200, "pinned": True},
                {"user_ids": [8], "max_message_id": 100, "pinned": False},
                {"user_ids": [19], "max_message_id": -(2**63), "pinned": False},
            ],
        )

    def test_seeded_conversation_rows(self) -> None:
        rng = random.Random(20261122)
        for _ in range(5000):
            me = rng.randrange(12)
            recent = {
                recipient: rng.randrange(10)
                for recipient in rng.sample(range(32), rng.randrange(18))
            }
            params = [(recipient, me, bool(rng.getrandbits(1))) for recipient in recent]
            rows = params + [
                (rng.choice([*recent, -1]), rng.randrange(12), bool(rng.getrandbits(1)))
                for _ in range(rng.randrange(45))
            ]
            rng.shuffle(rows)
            self.compare(me, recent, rows)

    def test_unusual_inputs_and_older_extension_defer(self) -> None:
        self.assertIsNone(self.bridge.assemble(1, {50: 2**80}, [(50, 1, True)]))
        self.assertIsNone(self.bridge.assemble(1, {50: 100}, [(50, 1, None)]))
        self.assertIsNone(
            self.bridge.finalize({frozenset({2**80}): {"max_message_id": 4, "pinned": False}})
        )
        disabled = load_bridge(enabled=False)
        self.assertFalse(disabled.assembly_available())
        self.assertIsNone(disabled.assemble(1, {}, []))
        self.assertIsNone(disabled.finalize({}))
        with patch.object(self.bridge, "_backend", SimpleNamespace()):
            self.assertFalse(self.bridge.assembly_available())
            self.assertIsNone(self.bridge.assemble(1, {}, []))
            self.assertIsNone(self.bridge.finalize({}))
