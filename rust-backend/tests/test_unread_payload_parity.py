# ruff: noqa: INP001
"""Compare native unread payload formatting with the checked-out Python algorithms."""

import ast
import json
import os
import random
import unittest
from dataclasses import asdict, dataclass
from importlib import util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TypeAlias
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
FALLBACK_TOPIC = "general chat"
UnreadRows: TypeAlias = dict[str, object]


@dataclass
class Details:
    type: str
    mentioned: bool | None = None
    user_ids: list[int] | None = None
    stream_id: int | None = None
    topic: str | None = None
    unmuted_stream_msg: bool | None = None


def load_bridge(*, enabled: bool) -> ModuleType:
    path = ROOT / "zerver/lib/native_unread.py"
    spec = util.spec_from_file_location("native_unread", path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    with patch.dict(os.environ, {"ZULIP_RUST_UNREAD_PAYLOAD": str(int(enabled))}):
        spec.loader.exec_module(module)
    return module


def load_formatters(module: ModuleType, path: Path) -> ModuleType:
    source = ast.parse(path.read_text(), filename=str(path))
    helpers = {
        "aggregate_streams",
        "aggregate_pms",
        "aggregate_direct_message_groups",
        "aggregate_unread_data",
        "format_unread_message_details",
    }
    funcs = [
        node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in helpers
    ]
    scope = ModuleType("unread_formatters")
    vars(scope).update(
        dict.fromkeys(
            (
                "RawUnreadStreamDict",
                "RawUnreadDirectMessageDict",
                "RawUnreadDirectMessageGroupDict",
                "RawUnreadMessagesResult",
                "UnreadStreamInfo",
                "UnreadDirectMessageInfo",
                "UnreadDirectMessageGroupInfo",
                "UnreadMessagesResult",
            ),
            dict,
        ),
        Message=SimpleNamespace(EMPTY_TOPIC_FALLBACK_NAME=FALLBACK_TOPIC),
        MessageDetails=Details,
        native_aggregate_unread=module.aggregate,
        native_format_unread_details=module.format_details,
    )
    exec(compile(ast.Module(funcs, []), str(path), "exec"), vars(scope))  # noqa: S102
    return scope


def raw(
    *,
    pms: dict[int, dict[str, int]] | None = None,
    streams: dict[int, dict[str, int | str]] | None = None,
    huddles: dict[int, dict[str, str]] | None = None,
    mentions: set[int] | None = None,
    unmuted: set[int] | None = None,
    old_missing: bool = False,
) -> UnreadRows:
    return {
        "pm_dict": pms or {},
        "stream_dict": streams or {},
        "huddle_dict": huddles or {},
        "mentions": mentions or set(),
        "muted_stream_ids": set(),
        "unmuted_stream_msgs": unmuted or set(),
        "old_unreads_missing": old_missing,
    }


class UnreadPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = ROOT / "zerver/lib/message.py"
        cls.bridge = load_bridge(enabled=True)
        assert cls.bridge._backend is not None
        cls.native = load_formatters(cls.bridge, source)
        cls.disabled = load_bridge(enabled=False)
        baseline = Path(os.environ.get("ZULIP_UNREAD_PARITY_REFERENCE", source))
        cls.reference = load_formatters(cls.disabled, baseline)

    def assert_payloads_equal(self, rows: UnreadRows, my_user_id: int = 2) -> None:
        for allow_empty in (True, False):
            result = self.bridge.aggregate(rows, allow_empty, FALLBACK_TOPIC)
            self.assertIsNotNone(result)
            expected = self.reference.aggregate_unread_data(rows, allow_empty)
            self.assertEqual(json.dumps(result), json.dumps(expected))
            self.assertEqual(self.native.aggregate_unread_data(rows, allow_empty), expected)

        details = self.bridge.format_details(my_user_id, rows, Details)
        self.assertIsNotNone(details)
        expected_details = self.reference.format_unread_message_details(my_user_id, rows)
        self.assertEqual(
            [(key, asdict(value)) for key, value in details.items()],
            [(key, asdict(value)) for key, value in expected_details.items()],
        )
        self.assertEqual(
            self.native.format_unread_message_details(my_user_id, rows), expected_details
        )

    def test_first_topic_spelling_and_legacy_empty_topic(self) -> None:
        rows = raw(
            pms={30: {"other_user_id": 9}, 20: {"other_user_id": 9}},
            streams={
                80: {"stream_id": 4, "topic": "GENERAL CHAT"},
                4: {"stream_id": 4, "topic": ""},
                70: {"stream_id": 4, "topic": "ΟΣ"},
                3: {"stream_id": 4, "topic": "ος"},
                55: {"stream_id": 4, "topic": "οσ"},
                44: {"stream_id": 4, "topic": "İ"},
                2: {"stream_id": 4, "topic": "i\u0307"},
                33: {"stream_id": 7, "topic": "GENERAL CHAT"},
            },
            huddles={42: {"user_ids_string": "8,10,2"}, 18: {"user_ids_string": "2,10,8"}},
            mentions={30, 4, 70, 18},
            unmuted={80, 4, 55, 44},
            old_missing=True,
        )
        self.assert_payloads_equal(rows)
        result = self.native.aggregate_unread_data(rows, False)
        self.assertEqual(
            result["streams"][0],
            {"stream_id": 4, "topic": "GENERAL CHAT", "unread_message_ids": [4, 80]},
        )
        self.assertEqual(result["pms"][0]["sender_id"], 9)
        self.assertEqual(result["count"], 8)
        details = self.native.format_unread_message_details(2, rows)
        self.assertEqual(
            list(details), ["30", "20", "80", "4", "70", "3", "55", "44", "2", "33", "42", "18"]
        )
        self.assertEqual(details["42"].user_ids, [8, 10])
        self.assertEqual(details["4"].topic, "")
        self.assertIs(details["4"].mentioned, True)
        self.assertIs(details["3"].mentioned, None)

    def test_seeded_ordered_payloads(self) -> None:
        rng = random.Random(409315)
        topics = [
            "",
            "General CHAT",
            "GENERAL chat",
            "Zulip",
            "zulip",
            "ΟΣ",
            "ος",
            "οσ",
            "İ",
            "i\u0307",
            "ΙΣ Α",
            "Straße",
            "STRASSE",
            "پیام",
            "🌱",
            "\0",
        ]
        for _ in range(4000):
            ids = rng.sample(range(-50, 100), rng.randrange(31))
            pms = {}
            streams = {}
            huddles = {}
            mentions = set()
            unmuted = set()
            my_user_id = rng.randrange(10)
            for message_id in ids:
                kind = rng.randrange(3)
                if kind == 0:
                    pms[message_id] = {"other_user_id": rng.randrange(10)}
                elif kind == 1:
                    streams[message_id] = {
                        "stream_id": rng.randrange(5),
                        "topic": rng.choice(topics),
                    }
                    if rng.getrandbits(1):
                        unmuted.add(message_id)
                else:
                    user_ids = [my_user_id, *rng.choices(range(20), k=3)]
                    rng.shuffle(user_ids)
                    huddles[message_id] = {"user_ids_string": ",".join(map(str, user_ids))}
                if rng.randrange(3) == 0:
                    mentions.add(message_id)
            self.assert_payloads_equal(
                raw(
                    pms=pms,
                    streams=streams,
                    huddles=huddles,
                    mentions=mentions,
                    unmuted=unmuted,
                    old_missing=bool(rng.getrandbits(1)),
                ),
                my_user_id,
            )

    def test_empty_and_capped_snapshot(self) -> None:
        self.assert_payloads_equal(raw())
        streams = {i: {"stream_id": 1, "topic": "start"} for i in reversed(range(50_000))}
        rows = raw(streams=streams, unmuted={1, 2}, old_missing=True)
        native = self.bridge.aggregate(rows, True, FALLBACK_TOPIC)
        self.assertIsNotNone(native)
        self.assertEqual(native, self.reference.aggregate_unread_data(rows, True))
        self.assertEqual(native["streams"][0]["unread_message_ids"], list(range(50_000)))

    def test_unusual_python_values_and_old_extension_fall_back(self) -> None:
        rows = raw(streams={2: {"stream_id": 1, "topic": "\ud800"}}, mentions={2})
        self.assertIsNone(self.bridge.aggregate(rows, True, FALLBACK_TOPIC))
        self.assertEqual(
            self.native.aggregate_unread_data(rows, True),
            self.reference.aggregate_unread_data(rows, True),
        )
        wide = raw(pms={1 << 70: {"other_user_id": 2}})
        self.assertIsNone(self.bridge.aggregate(wide, False, FALLBACK_TOPIC))
        self.assertIsNone(self.bridge.format_details(2, wide, Details))
        self.assertEqual(
            self.native.format_unread_message_details(2, wide),
            self.reference.format_unread_message_details(2, wide),
        )
        unicode_ids = raw(huddles={33: {"user_ids_string": "٢, ۳,11"}})
        self.assertIsNone(self.bridge.format_details(2, unicode_ids, Details))
        self.assertEqual(
            self.native.format_unread_message_details(2, unicode_ids)["33"].user_ids, [3, 11]
        )
        with patch.object(self.bridge, "_backend", SimpleNamespace()):
            self.assertEqual(
                self.native.aggregate_unread_data(wide, True),
                self.reference.aggregate_unread_data(wide, True),
            )
        self.assertIsNone(self.disabled.aggregate(raw(), True, FALLBACK_TOPIC))
        self.assertIsNone(self.disabled.format_details(2, raw(), Details))
