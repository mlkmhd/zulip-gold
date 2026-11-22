# ruff: noqa: INP001
"""Compare the checked-out pure presence formatters without Django services."""

import ast
import itertools
import json
import os
import random
import unittest
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from importlib import util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TypeAlias
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
Row: TypeAlias = dict[str, object]


class UserPresence:
    LEGACY_STATUS_ACTIVE = "active"
    LEGACY_STATUS_IDLE = "idle"


def load_bridge(*, enabled: bool) -> ModuleType:
    path = ROOT / "zerver/lib/native_presence.py"
    spec = util.spec_from_file_location("native_presence", path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    with patch.dict(os.environ, {"ZULIP_RUST_PRESENCE_FORMAT": str(int(enabled))}):
        spec.loader.exec_module(module)
    return module


def load_formatters(backend: ModuleType, *, source_root: Path = ROOT) -> ModuleType:
    source = source_root / "zerver/lib/presence.py"
    timestamp_source = source_root / "zerver/lib/timestamp.py"
    funcs = {
        "get_presence_dicts_for_rows",
        "get_modern_user_presence_info",
        "get_legacy_user_presence_info",
        "format_legacy_presence_dict",
        "_legacy_spec",
        "user_presence_datetime_with_date_joined_default",
    }
    module = ModuleType("presence")
    vars(module).update(
        native_presence=backend,
        settings=SimpleNamespace(PRESENCE_LEGACY_EVENT_OFFSET_FOR_ACTIVITY_SECONDS=70),
        Any=object,
        Mapping=Mapping,
        Sequence=Sequence,
        timedelta=timedelta,
        timezone=timezone,
        datetime=datetime,
        UserPresence=UserPresence,
    )
    timestamp_tree = ast.parse(timestamp_source.read_text())
    timestamp_nodes = [
        node
        for node in timestamp_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        and node.name in ("verify_UTC", "datetime_to_timestamp", "TimeZoneNotUTCError")
    ]
    presence_tree = ast.parse(source.read_text())
    presence_nodes = [
        node
        for node in presence_tree.body
        if (
            (isinstance(node, ast.FunctionDef) and node.name in funcs)
            or (
                isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.startswith("_REFERENCE_")
            )
        )
    ]
    # Compile only the selected checked-out helpers, not any fixture data.
    exec(compile(ast.Module(timestamp_nodes, []), str(timestamp_source), "exec"), vars(module))  # noqa: S102
    exec(compile(ast.Module(presence_nodes, []), str(source), "exec"), vars(module))  # noqa: S102
    return module


def row(
    user_id: int,
    email: str,
    active: datetime | None,
    connected: datetime | None,
    joined: datetime = EPOCH,
) -> Row:
    return {
        "user_profile_id": user_id,
        "user_profile__email": email,
        "last_active_time": active,
        "last_connected_time": connected,
        "user_profile__date_joined": joined,
        "not_part_of_response": False,
    }


class PresenceParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = load_bridge(enabled=True)
        assert cls.bridge._backend is not None
        cls.native = load_formatters(cls.bridge)
        cls.reference = load_formatters(load_bridge(enabled=False))

    def assert_ordered_equal(self, received: object, expected: object) -> None:
        self.assertEqual(json.dumps(received), json.dumps(expected))

    def test_legacy_inclusive_threshold_at_microsecond_precision(self) -> None:
        active = EPOCH - timedelta(microseconds=500_001)
        for extra, expected in ((-1, "active"), (0, "active"), (1, "idle")):
            connected = active + timedelta(seconds=70, microseconds=extra)
            result = self.native.format_legacy_presence_dict(active, connected)
            self.assertEqual(result["status"], expected)
            self.assert_ordered_equal(
                result, self.reference.format_legacy_presence_dict(active, connected)
            )
            self.assert_ordered_equal(
                self.native.get_legacy_user_presence_info(active, connected),
                self.reference.get_legacy_user_presence_info(active, connected),
            )
        self.assertEqual(
            self.native.get_modern_user_presence_info(active, EPOCH)["active_timestamp"], 0
        )

    def test_rows_preserve_order_defaults_and_last_duplicate(self) -> None:
        later = EPOCH + timedelta(seconds=120)
        rows = [
            row(8, "sender@example.test", None, later),
            row(31, "گیرنده@example.test", EPOCH, None),
            row(8, "sender@example.test", later, later, joined=later),
        ]
        for slim in (True, False):
            result = self.native.get_presence_dicts_for_rows(rows, slim)
            self.assert_ordered_equal(
                result, self.reference.get_presence_dicts_for_rows(rows, slim)
            )
        self.assertEqual(list(self.native.get_presence_dicts_for_rows(rows, True)), ["8", "31"])
        self.assertEqual(
            self.native.get_presence_dicts_for_rows(rows, True)["8"],
            {"active_timestamp": 120, "idle_timestamp": 120},
        )
        for slim in (True, False):
            self.assertEqual(self.native.get_presence_dicts_for_rows([], slim), {})

    def test_seeded_ordered_presence_rows(self) -> None:
        rng = random.Random(780318)
        accepted = 0
        native_format = self.bridge._backend.format_presence_rows

        def observe(*args: object) -> object:
            nonlocal accepted
            rendered = native_format(*args)
            if rendered is not None:
                accepted += 1
            return rendered

        with patch.object(self.bridge._backend, "format_presence_rows", observe):
            for _ in range(8000):
                rows = []
                for _ in range(rng.randrange(8)):
                    user_id = rng.randrange(7)
                    base = EPOCH + timedelta(
                        seconds=rng.randrange(-8_300_000_000, 8_300_000_000),
                        microseconds=rng.randrange(1_000_000),
                    )
                    active = base if rng.randrange(4) else None
                    connected = base + timedelta(
                        seconds=rng.choice((-71, -70, -1, 0, 1, 69, 70, 71)),
                        microseconds=rng.choice((-1, 0, 1, 130)),
                    )
                    rows.append(
                        row(
                            user_id,
                            f"sample-{user_id}@example.test",
                            active,
                            connected,
                            joined=base,
                        )
                    )
                for slim in (True, False):
                    self.assert_ordered_equal(
                        self.native.get_presence_dicts_for_rows(rows, slim),
                        self.reference.get_presence_dicts_for_rows(rows, slim),
                    )
        self.assertEqual(accepted, 16_000)

    def test_offsets_and_timezones_keep_reference_errors(self) -> None:
        for offset, microseconds in itertools.product(
            (-1_000_001, -70, 0, 70, 1_000_001, 0.125), (-1, 0, 1, 123)
        ):
            connected = EPOCH + timedelta(seconds=offset, microseconds=microseconds)
            with (
                patch.object(
                    self.native.settings,
                    "PRESENCE_LEGACY_EVENT_OFFSET_FOR_ACTIVITY_SECONDS",
                    offset,
                ),
                patch.object(
                    self.reference.settings,
                    "PRESENCE_LEGACY_EVENT_OFFSET_FOR_ACTIVITY_SECONDS",
                    offset,
                ),
            ):
                self.assert_ordered_equal(
                    self.native.format_legacy_presence_dict(EPOCH, connected),
                    self.reference.format_legacy_presence_dict(EPOCH, connected),
                )
        for invalid in (
            EPOCH.replace(tzinfo=None),
            EPOCH.replace(tzinfo=timezone(timedelta(hours=3))),
        ):
            with self.assertRaises(self.native.TimeZoneNotUTCError):
                self.native.get_modern_user_presence_info(invalid, EPOCH)
        ancient = datetime(1, 1, 1, tzinfo=timezone.utc)
        future = datetime.max.replace(tzinfo=timezone.utc)
        self.assert_ordered_equal(
            self.native.get_modern_user_presence_info(ancient, future),
            self.reference.get_modern_user_presence_info(ancient, future),
        )
        with self.assertRaises(OverflowError):
            self.native.get_legacy_user_presence_info(future, ancient)
        subclass = type("CustomDatetime", (datetime,), {})
        custom = subclass(2026, 1, 1, tzinfo=timezone.utc)
        policy = (datetime, timezone.utc)
        self.assertIsNone(
            self.bridge._backend.format_presence_information(custom, EPOCH, policy, None, False)
        )
        self.assertIsNone(
            self.bridge._backend.format_presence_rows([row(2, "match", None, future)], policy, None)
        )

    def test_single_user_legacy_offline_applies_only_to_aggregate(self) -> None:
        modern = {"active_timestamp": 240, "idle_timestamp": 290}
        for duration in (299, 300, 301):
            with self.subTest(duration=duration):
                legacy = {
                    "aggregated": {"client": "website", "status": "idle", "timestamp": 290},
                    "website": {
                        "client": "website",
                        "status": "idle",
                        "timestamp": 290,
                        "pushable": False,
                    },
                }
                result = self.bridge.finish_single_user(modern, legacy, duration, 300)
                assert result is not None
                self.assertEqual(
                    list(result), ["active_timestamp", "idle_timestamp", "aggregated", "website"]
                )
                self.assertIs(result["aggregated"], legacy["aggregated"])
                self.assertEqual(list(result["aggregated"]), ["status", "timestamp"])
                self.assertEqual(
                    result["aggregated"]["status"], "offline" if duration > 300 else "idle"
                )
                self.assertEqual(result["website"], {"status": "idle", "timestamp": 290})
                self.assertEqual(modern, {"active_timestamp": 240, "idle_timestamp": 290})

        malformed = {"aggregated": {"status": "idle"}, "website": None}
        self.assertIsNone(self.bridge.finish_single_user(modern, malformed, 301, 300))
        self.assertEqual(malformed, {"aggregated": {"status": "idle"}, "website": None})

    def test_custom_callbacks_and_old_wheel_remain_in_python(self) -> None:
        rows = [row(1, "sender@example.test", EPOCH, EPOCH)]
        backend = self.bridge._backend
        original_rows = backend.format_presence_rows
        original_single = backend.format_presence_information
        with (
            patch.object(backend, "format_presence_rows", wraps=original_rows) as batch,
            patch.object(backend, "format_presence_information", wraps=original_single) as single,
        ):
            self.native.get_presence_dicts_for_rows(rows, True)
            batch.assert_called_once()
            self.native.format_legacy_presence_dict(EPOCH, EPOCH)
            single.assert_called_once()
            with patch.object(self.native, "datetime_to_timestamp", return_value=27) as timestamp:
                self.assertEqual(
                    self.native.get_presence_dicts_for_rows(rows, True),
                    {"1": {"active_timestamp": 27, "idle_timestamp": 27}},
                )
                self.assertEqual(timestamp.call_count, 2)
            with patch.object(self.native, "timedelta", wraps=timedelta) as offset:
                self.assertEqual(
                    self.native.get_presence_dicts_for_rows(rows, False),
                    self.reference.get_presence_dicts_for_rows(rows, False),
                )
                offset.assert_called_once_with(seconds=70)
            batch.assert_called_once()
            single.assert_called_once()
        with patch.object(self.bridge, "_backend", ModuleType("previous_wheel")):
            self.assert_ordered_equal(
                self.native.get_presence_dicts_for_rows(rows, False),
                self.reference.get_presence_dicts_for_rows(rows, False),
            )


if __name__ == "__main__":
    unittest.main()
