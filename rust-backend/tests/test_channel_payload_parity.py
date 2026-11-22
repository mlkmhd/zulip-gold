# ruff: noqa: INP001
"""Check production channel formatters against the corresponding Python code."""

import ast
import json
import os
import random
import unittest
from datetime import datetime, timedelta, timezone
from importlib import util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)


def load_bridge(*, enabled: bool) -> ModuleType:
    path = ROOT / "zerver/lib/native_channel_payload.py"
    spec = util.spec_from_file_location("native_channel_payload", path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    with patch.dict(os.environ, {"ZULIP_RUST_CHANNEL_PAYLOAD": str(int(enabled))}):
        spec.loader.exec_module(module)
    return module


def load_formatters(source: Path, bridge: ModuleType) -> tuple[ModuleType, list[str], list[str]]:
    module = ModuleType("channel_formatters")
    vars(module).update(
        UserProfile=object,
        RawSubscriptionDict=dict,
        APIStreamDict=dict,
        SubscriptionStreamDict=dict,
        datetime=datetime,
        timezone_now=lambda: NOW,
        native_channel_payload=bridge,
        STREAM_TRAFFIC_CALCULATION_MIN_AGE_DAYS=7,
    )
    sub_fields: list[str] = []
    stream_fields: list[str] = []
    for file, names in [
        ("subscription_info.py", {"build_stream_dict_for_sub"}),
        (
            "stream_traffic.py",
            {"get_average_weekly_stream_traffic", "round_to_2_significant_digits"},
        ),
    ]:
        path = source / file
        tree = ast.parse(path.read_text(), filename=str(path))
        selected = [
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        if file == "subscription_info.py":
            for node in ast.walk(selected[0]):
                if (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name)
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)
                ):
                    if node.value.id == "sub_dict":
                        sub_fields.append(node.slice.value)
                    if node.value.id == "stream_dict":
                        stream_fields.append(node.slice.value)
        exec(compile(ast.Module(selected, []), str(path), "exec"), vars(module))  # noqa: S102
    return module, sub_fields, stream_fields


class ChannelPayloadParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        current = ROOT / "zerver/lib"
        baseline = Path(os.environ.get("ZULIP_CHANNEL_REFERENCE_DIR", current))
        cls.bridge = load_bridge(enabled=True)
        assert cls.bridge._backend is not None
        cls.native, _, _ = load_formatters(current, cls.bridge)
        cls.reference, cls.sub_fields, cls.stream_fields = load_formatters(
            baseline, load_bridge(enabled=False)
        )

    def test_seeded_subscription_projection_preserves_aliases_and_order(self) -> None:
        rng = random.Random(160227)
        for i in range(4000):
            sub = dict.fromkeys(self.sub_fields)
            stream = dict.fromkeys(self.stream_fields)
            sub.update(
                is_muted=bool(rng.getrandbits(1)),
                color=rng.choice(("#76ce90", "", "#e79ab5")),
                raw_subscription_metadata="not an API field",
            )
            group = {"direct_members": [i, i + 8], "direct_subgroups": [1, 2]}
            stream.update(
                stream_id=i,
                name="نمونهٔ کانال",
                description="🌱",
                raw_channel_metadata="not an API field",
                can_administer_channel_group=group,
            )
            for field in ("desktop_notifications", "email_notifications", "push_notifications"):
                sub[field] = rng.choice((True, False, None))
            expected = self.reference.build_stream_dict_for_sub(None, sub, stream)
            result = self.bridge.assemble_subscription(sub, stream)
            self.assertIsNotNone(result)
            self.assertEqual(json.dumps(result), json.dumps(expected))
            self.assertIs(result["can_administer_channel_group"], group)
            self.assertEqual(self.native.build_stream_dict_for_sub(None, sub, stream), expected)
            self.assertNotIn("raw_channel_metadata", result)
            self.assertNotIn("raw_subscription_metadata", result)

    def test_weekly_traffic_preserves_signed_integer_and_boundary_math(self) -> None:
        rng = random.Random(20261122)
        for age in (-1, 0, 6, 7, 8, 13, 27, 28, 29, 300):
            created = NOW - timedelta(days=age)
            boundaries = [-(2**63), -135, -125, -35, -1, 0, 1, 100, 125, 135, 2**63 - 1]
            for count in [*boundaries, *(rng.randrange(-(2**63), 2**63) for _ in range(1000))]:
                observed = self.bridge.average_weekly_traffic(age, count)
                self.assertIsNotNone(observed)
                expected = self.reference.get_average_weekly_stream_traffic(
                    42, created, {42: count}
                )
                self.assertEqual(observed, (expected,))
                self.assertEqual(
                    self.native.get_average_weekly_stream_traffic(42, created, {42: count}),
                    expected,
                )

    def test_older_extension_and_unusual_python_values_fall_back(self) -> None:
        stream = dict.fromkeys(self.stream_fields)
        sub = dict.fromkeys(self.sub_fields)
        sub["is_muted"] = 1
        self.assertIsNone(self.bridge.assemble_subscription(sub, stream))
        self.assertFalse(self.native.build_stream_dict_for_sub(None, sub, stream)["in_home_view"])
        huge = 2**80
        self.assertIsNone(self.bridge.average_weekly_traffic(28, huge))
        created = NOW - timedelta(days=28)
        self.assertEqual(
            self.native.get_average_weekly_stream_traffic(42, created, {42: huge}),
            self.reference.get_average_weekly_stream_traffic(42, created, {42: huge}),
        )
        with patch.object(self.bridge, "_backend", SimpleNamespace()):
            self.assertIsNone(self.bridge.assemble_subscription(sub, stream))
            self.assertIsNone(self.bridge.average_weekly_traffic(28, 4))
