# ruff: noqa: INP001
"""Check register formatting against the Python implementation and its aliases."""

import ast
import json
import os
import random
import unittest
from collections.abc import Callable
from copy import deepcopy
from importlib import util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
FALLBACK = "general chat"
FIELDS = (
    "desktop_notifications",
    "audible_notifications",
    "push_notifications",
    "email_notifications",
)


def load_bridge(*, enabled: bool) -> ModuleType:
    path = ROOT / "zerver/lib/native_register_state.py"
    spec = util.spec_from_file_location("native_register_state", path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    with patch.dict(os.environ, {"ZULIP_RUST_REGISTER_STATE": str(int(enabled))}):
        spec.loader.exec_module(module)
    return module


def format_state(source: Path, bridge: ModuleType) -> ModuleType:
    scope = ModuleType("register_formatters")
    vars(scope).update(
        UserProfile=object,
        Any=object,
        Message=SimpleNamespace(EMPTY_TOPIC_FALLBACK_NAME=FALLBACK),
        native_register_state=bridge,
    )
    for file, name in [
        ("stream_subscription.py", "handle_stream_notifications_compatibility"),
        ("events.py", "post_process_state"),
    ]:
        path = source / file
        tree = ast.parse(path.read_text(), filename=str(path))
        function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
        )
        exec(compile(ast.Module([function], []), str(path), "exec"), vars(scope))  # noqa: S102
    return scope


class Profile:
    def __init__(self, defaults: dict[str, bool | None]) -> None:
        self.defaults = defaults
        self.reads: list[str] = []

    def __getattr__(self, name: str) -> bool | None:
        self.reads.append(name)
        return self.defaults[name]


class RegisterStateParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = ROOT / "zerver/lib"
        baseline = Path(os.environ.get("ZULIP_REGISTER_REFERENCE_DIR", source))
        cls.bridge = load_bridge(enabled=True)
        assert cls.bridge._backend is not None
        cls.native = format_state(source, cls.bridge)
        cls.reference = format_state(baseline, load_bridge(enabled=False))

    def test_user_sort_preserves_identity_and_removes_only_active_field(self) -> None:
        first = {"full_name": "First", "user_id": 7, "is_active": True}
        second = {"full_name": "Inactive", "user_id": 3, "is_active": False}
        third = {"full_name": "Third", "user_id": 7, "is_active": True}
        fourth = {"full_name": "Fourth", "user_id": 0, "is_active": False}
        result = self.bridge.partition_users({5: first, 1: second, 2: third, 0: fourth})
        self.assertIsNotNone(result)
        active, inactive = result
        self.assertEqual(active, [first, third])
        self.assertEqual(inactive, [fourth, second])
        self.assertIs(active[0], first)
        self.assertIs(active[1], third)
        self.assertIs(inactive[0], fourth)
        self.assertIs(inactive[1], second)
        self.assertEqual(list(first), ["full_name", "user_id"])
        self.assertNotIn("is_active", second)

    def test_seeded_register_snapshots(self) -> None:
        rng = random.Random(492629)
        accepted = [0, 0, 0]
        backend = self.bridge._backend

        def track(index: int, fn: Callable[..., object]) -> Callable[..., object]:
            def result(*args: object) -> object:
                state = fn(*args)
                if state is not None and state is not False:
                    accepted[index] += 1
                return state

            return result

        with (
            patch.object(
                backend, "partition_register_users", track(0, backend.partition_register_users)
            ),
            patch.object(
                backend,
                "fill_legacy_register_notifications",
                track(1, backend.fill_legacy_register_notifications),
            ),
            patch.object(
                backend,
                "fill_legacy_register_topics",
                track(2, backend.fill_legacy_register_topics),
            ),
        ):
            for i in range(4000):
                rows = [
                    {key: rng.choice((True, False, None)) for key in FIELDS}
                    for _ in range(rng.randrange(8))
                ]
                state = {
                    "raw_users": {
                        i: {"user_id": rng.randrange(100), "name": "نمونه", "is_active": i % 2 == 0}
                        for i in range(rng.randrange(12))
                    },
                    "subscriptions": rows,
                    "unsubscribed": rows[::2],
                    "user_topics": [
                        {"topic_name": rng.choice(("", FALLBACK, "🪴", "نمونه")), "stream_id": i}
                        for i in range(rng.randrange(8))
                    ],
                }
                defaults = {
                    f"enable_stream_{key}": rng.choice((True, False, None)) for key in FIELDS
                }
                expected = deepcopy(state)
                logged_in = i % 3 != 0
                native_profile = Profile(defaults) if logged_in else None
                reference_profile = Profile(defaults) if logged_in else None
                capability_null = bool(i & 1)
                capability_empty = bool(i & 2)
                self.native.post_process_state(
                    native_profile, state, capability_null, capability_empty
                )
                self.reference.post_process_state(
                    reference_profile, expected, capability_null, capability_empty
                )
                self.assertEqual(json.dumps(state), json.dumps(expected))
                if native_profile is not None and reference_profile is not None:
                    self.assertEqual(native_profile.reads, reference_profile.reads)
        self.assertEqual(accepted, [4000, 2000, 2000])

    def test_invalid_rows_defer_without_mutating_and_old_wheel_falls_back(self) -> None:
        user = {"user_id": 8, "is_active": True}
        self.assertIsNone(self.bridge.partition_users({1: user, 2: user}))
        self.assertTrue(user["is_active"])
        row = dict.fromkeys(FIELDS)
        self.assertFalse(self.bridge.fill_legacy_notifications([row, {}], [], None))
        self.assertEqual(row, dict.fromkeys(FIELDS))
        topic = {"topic_name": ""}
        self.assertFalse(self.bridge.fill_legacy_topics([topic, {}], FALLBACK))
        self.assertEqual(topic["topic_name"], "")
        with patch.object(self.bridge, "_backend", SimpleNamespace()):
            self.assertIsNone(self.bridge.partition_users({}))
            self.assertFalse(self.bridge.fill_legacy_notifications([], [], None))
            self.assertFalse(self.bridge.fill_legacy_topics([], FALLBACK))
