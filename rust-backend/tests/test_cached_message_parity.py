# ruff: noqa: INP001
"""Exercise actual cache and event serializers without services."""

import ast
import copy
import json
import random
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TypeAlias
from unittest.mock import patch

from test_message_payload_parity import ROOT, load_bridge
from test_mobile_reply_links import PLAIN, REPLY, load_reply_links

Payload: TypeAlias = dict[str, object]


def take_method(module: ast.Module, owner: str, name: str) -> ast.FunctionDef:
    klass = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == owner
    )
    return next(
        node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name == name
    )


def load_serializers(
    bridge: ModuleType, replies: ModuleType, *, source_root: Path = ROOT
) -> ModuleType:
    cache_path = source_root / "zerver/lib/message_cache.py"
    event_path = source_root / "zerver/tornado/event_queue.py"
    cache_tree = ast.parse(cache_path.read_text(), filename=str(cache_path))
    queue_tree = ast.parse(event_path.read_text(), filename=str(event_path))
    reactions = next(
        node
        for node in cache_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ReactionDict"
    )
    frame = take_method(cache_tree, "MessageDict", "build_message_dict")
    frame.decorator_list = []
    format_events = take_method(queue_tree, "ClientDescriptor", "events_for_client")
    events: list[object] = []
    namespace = ModuleType("message_serializers")

    def timestamp(value: datetime) -> int:
        events.append(("timestamp", value))
        return int(value.timestamp())

    def topic_links(realm: int, topic: str) -> list[Payload]:
        events.append(("topic", realm, topic))
        return [{"url": "https://example.test/", "text": topic}]

    def select_related(*fields: str) -> SimpleNamespace:
        events.append(("select", fields))
        return SimpleNamespace(get=lambda *, id: id)

    def render(message_id: int, content: str) -> str:
        events.append(("render", message_id))
        return f"<p>{content}</p>"

    def status(content: str, _rendered: str) -> bool:
        return content.startswith("/me ")

    vars(namespace).update(
        Any=object,
        EditHistoryEvent=dict,
        RawReactionRow=dict,
        datetime=datetime,
        datetime_to_timestamp=timestamp,
        TOPIC_NAME="subject",
        TOPIC_LINKS="topic_links",
        topic_links=topic_links,
        orjson=json,
        markdown_version=401,
        Message=SimpleNamespace(
            objects=SimpleNamespace(select_related=select_related), is_status_message=status
        ),
        save_message_rendered_content=render,
        native_message_payload=bridge,
        reply_links=replies,
        needs_plain_reply_links=replies.needs_plain_reply_links,
        plain_reply_link_fields=replies.plain_reply_link_fields,
        _REFERENCE_STATUS_MESSAGE=status,
        _REFERENCE_EVENT_REPLY_FIELDS=replies.plain_reply_link_fields,
        trace=events,
    )
    # Only the checked-out serializer definitions are compiled; no fixture text is code.
    selected = ast.Module([reactions, frame, format_events], [])
    exec(compile(selected, str(cache_path), "exec"), vars(namespace))  # noqa: S102
    namespace._REFERENCE_REACTION_ROW = namespace.ReactionDict.build_dict_from_raw_db_row
    return namespace


def raw_frame() -> Payload:
    return {
        "message_id": 200,
        "last_edit_time": None,
        "edit_history_json": None,
        "content": "/me learns Rust",
        "topic_name": "موضوع",
        "date_sent": datetime.fromtimestamp(-0.5, timezone.utc),
        "rendered_content": "<p>Rust</p>",
        "rendered_content_version": 401,
        "sender_id": 17,
        "sender_realm_id": 3,
        "sending_client_name": "sample client",
        "rendering_realm_id": 7,
        "recipient_id": 310,
        "recipient_type": 2,
        "recipient_type_id": 312,
        "reactions": [
            {
                "emoji_name": "penguin",
                "emoji_code": "1f427",
                "reaction_type": "unicode_emoji",
                "user_profile_id": 8,
                "message_id": 200,
            }
        ],
        "submessages": [{"id": 3, "message_id": 200, "content": "تست"}],
    }


def mobile_fixture() -> list[Payload]:
    return [
        {"type": "heartbeat"},
        {"type": "message", "message": {"content_type": "text/html", "content": REPLY}},
        {"type": "message", "message": {"content_type": "text/x-markdown", "content": REPLY}},
        {"type": "update_message", "orig_rendered_content": REPLY, "rendered_content": REPLY},
        {"type": "url_embed_data", "rendered_content": REPLY + "\ud800"},
        {"type": "message", "message": {"content_type": "text/html", "content": "<p>plain</p>"}},
    ]


def for_client(client: str, events: list[Payload]) -> SimpleNamespace:
    return SimpleNamespace(
        client_type_name=client, event_queue=SimpleNamespace(contents=lambda: events)
    )


class CachedMessageParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = load_bridge(enabled=True)
        cls.reference = load_serializers(
            load_bridge(enabled=False), load_reply_links(enabled=False)
        )
        cls.rust_python_reply = load_serializers(cls.bridge, load_reply_links(enabled=False))
        cls.native = load_serializers(cls.bridge, load_reply_links(enabled=True))
        assert cls.bridge._backend is not None

    def test_cache_frame_preserves_order_and_shared_objects(self) -> None:
        fields = raw_frame()
        expected = self.reference.build_message_dict(**fields)
        result = self.native.build_message_dict(**fields)
        self.assertEqual(json.dumps(result).encode(), json.dumps(expected).encode())
        self.assertTrue(result["is_me_message"])
        self.assertEqual(result["timestamp"], 0)
        self.assertIs(result["submessages"], fields["submessages"])
        self.assertEqual(
            list(result["reactions"][0]), ["emoji_name", "emoji_code", "reaction_type", "user_id"]
        )
        self.assertNotIn("message_id", result["reactions"][0])
        self.assertEqual(result["reactions"][0]["user_id"], 8)
        self.assertIn(("topic", 7, "موضوع"), self.native.trace)

    def test_side_effects_and_reference_fallback_are_not_repeated(self) -> None:
        args = raw_frame()
        args.update(
            content="/me سلام\ud800",
            rendered_content_version=320,
            last_edit_time=datetime.fromtimestamp(11.99, tz=timezone.utc),
            edit_history_json='[{"topic": "prior"}]',
        )
        self.reference.trace.clear()
        self.native.trace.clear()
        result = self.native.build_message_dict(**args)
        self.assertEqual(result, self.reference.build_message_dict(**args))
        self.assertEqual(self.native.trace, self.reference.trace)
        self.assertEqual(
            [event[0] for event in self.native.trace],
            ["timestamp", "topic", "timestamp", "select", "render"],
        )
        args["edit_history_json"] = "malformed"
        self.native.trace.clear()
        with self.assertRaises(ValueError):
            self.native.build_message_dict(**args)
        self.assertEqual(
            [event[0] for event in self.native.trace], ["timestamp", "topic", "timestamp"]
        )

    def test_missing_reaction_field_and_replaced_hooks(self) -> None:
        args = raw_frame()
        args.update(reactions=[{"emoji_name": "penguin"}], rendered_content_version=3)
        self.native.trace.clear()
        with self.assertRaises(KeyError) as error:
            self.native.build_message_dict(**args)
        self.assertEqual(error.exception.args, ("emoji_code",))
        self.assertEqual(self.native.trace.count(("render", 200)), 1)
        args = raw_frame()
        with patch.object(self.native.Message, "is_status_message", return_value=False) as status:
            result = self.native.build_message_dict(**args)
            self.assertFalse(result["is_me_message"])
            status.assert_called_once()
        with patch.object(
            self.native.ReactionDict,
            "build_dict_from_raw_db_row",
            return_value={"replacement": True},
        ) as maker:
            result = self.native.build_message_dict(**args)
            self.assertTrue(result["is_me_message"])
            self.assertEqual(result["reactions"], [{"replacement": True}])
            maker.assert_called_once()
        with patch.object(self.native, "TOPIC_NAME", "legacy_topic"):
            result = self.native.build_message_dict(**args)
            self.assertEqual(result["legacy_topic"], args["topic_name"])
            self.assertNotIn("subject", result)

    def test_seeded_frame_serialization(self) -> None:
        rng = random.Random(471803)
        for _ in range(3000):
            args = raw_frame()
            args["content"] = rng.choice(
                ("/me ", "/me🧪", "/me 🧪", "/me\tsleep", "سلام", "\ud800")
            )
            if rng.randrange(2):
                args["last_edit_time"] = args["date_sent"]
                args["edit_history_json"] = rng.choice(("{}", "null", '[{"topic": "سلام"}]', "[]"))
            if rng.randrange(2):
                args["rendered_content"] = None
            if rng.randrange(2):
                args["rendered_content_version"] = 490
            if rng.randrange(2):
                args["reactions"] = []
            args["recipient_type"] = rng.choice((2, 3))
            expected = self.reference.build_message_dict(**args)
            actual = self.native.build_message_dict(**args)
            self.assertEqual(json.dumps(actual), json.dumps(expected))

    def test_mobile_event_copies_only_changed_rows(self) -> None:
        expectation = self.reference.events_for_client(for_client("ZulipFlutter", mobile_fixture()))
        for cache in (self.rust_python_reply, self.native):
            events = mobile_fixture()
            shared = events.copy()
            after = cache.events_for_client(for_client("ZulipFlutter", events))
            self.assertEqual(after, expectation)
            self.assertIs(after, events)
            self.assertEqual(shared[1]["message"]["content"], REPLY)
            self.assertEqual(after[1]["message"]["content"], PLAIN)
            self.assertIs(after[0], shared[0])
            self.assertIs(after[2], shared[2])
            self.assertIs(after[5], shared[5])
            self.assertIsNot(after[1], shared[1])
            self.assertIsNot(after[3], shared[3])
            self.assertIsNot(after[4], shared[4])
            self.assertEqual(after[4]["rendered_content"], PLAIN + "\ud800")

    def test_seeded_mobile_batches(self) -> None:
        rng = random.Random(239317)
        for _ in range(1500):
            events = rng.choices(mobile_fixture(), k=rng.randrange(20))
            baseline = copy.deepcopy(events)
            migrated = copy.deepcopy(events)
            shared = migrated.copy()
            expected = self.reference.events_for_client(for_client("ZulipMobile", baseline))
            actual = self.native.events_for_client(for_client("ZulipMobile", migrated))
            self.assertEqual(actual, expected)
            self.assertEqual(shared, events)
            for event, original in zip(actual, shared, strict=True):
                if event == original:
                    self.assertIs(event, original)

    def test_event_callback_failure_is_not_replayed(self) -> None:
        events = mobile_fixture()
        call_count = 0

        def convert(rows: Payload, *fields: str) -> Payload:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("reply conversion failed")
            return self.native.plain_reply_link_fields(rows, *fields)

        with self.assertRaisesRegex(RuntimeError, "reply conversion failed"):
            self.bridge._backend.adapt_mobile_message_events(events, convert)
        self.assertEqual(call_count, 2)
        self.assertEqual(events[1]["message"]["content"], PLAIN)
        self.assertEqual(events[3]["rendered_content"], REPLY)

    def test_old_wheel_dispatch_and_unsupported_event(self) -> None:
        extension = self.bridge._backend
        with (
            patch.object(
                extension,
                "assemble_cached_message_frame",
                wraps=extension.assemble_cached_message_frame,
            ) as frame,
            patch.object(
                extension,
                "adapt_mobile_message_events",
                wraps=extension.adapt_mobile_message_events,
            ) as adapt,
        ):
            self.native.build_message_dict(**raw_frame())
            frame.assert_called_once()
            desktop = mobile_fixture()
            self.assertIs(self.native.events_for_client(for_client("website", desktop)), desktop)
            adapt.assert_not_called()
            self.native.events_for_client(for_client("ZulipFlutter", mobile_fixture()))
            adapt.assert_called_once()

        events = [*mobile_fixture(), {"unknown": None}]
        initial = copy.deepcopy(events)
        self.assertFalse(
            extension.adapt_mobile_message_events(events, self.native.plain_reply_link_fields)
        )
        self.assertEqual(events, initial)
        with self.assertRaises(KeyError):
            self.native.events_for_client(for_client("ZulipFlutter", events))
        self.assertEqual(events[1]["message"]["content"], PLAIN)

        with patch.object(self.bridge, "_backend", ModuleType("legacy_extension")):
            self.assertEqual(
                self.native.build_message_dict(**raw_frame()),
                self.reference.build_message_dict(**raw_frame()),
            )
            events = mobile_fixture()
            self.native.events_for_client(for_client("ZulipFlutter", events))
            self.assertEqual(events[1]["message"]["content"], PLAIN)


if __name__ == "__main__":
    unittest.main()
