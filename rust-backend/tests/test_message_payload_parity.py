# ruff: noqa: INP001
"""Exercise the real payload methods with service-free sender helpers."""

import ast
import copy
import itertools
import os
import random
import unittest
from collections.abc import Iterator
from email.headerregistry import Address
from importlib import import_module, util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import ClassVar, cast
from unittest.mock import ANY, patch

from test_mobile_reply_links import PLAIN, REPLY, load_reply_links
from typing_extensions import override

ROOT = Path(__file__).resolve().parents[2]


def load_bridge(*, enabled: bool) -> ModuleType:
    path = ROOT / "zerver/lib/native_message_payload.py"
    spec = util.spec_from_file_location("native_message_payload", path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    with patch.dict(os.environ, {"ZULIP_RUST_MESSAGE_PAYLOAD": str(int(enabled))}):
        spec.loader.exec_module(module)
    return module


def load_cache(bridge: ModuleType, replies: ModuleType) -> ModuleType:
    path = ROOT / "zerver/lib/message_cache.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    methods = {"sew_messages_and_reactions", "sew_messages_and_submessages"}
    outer = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in methods
    ]
    cls = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MessageDict"
    )
    avatar = ast.parse(
        """@staticmethod
def set_sender_avatar(obj, gravatar, visible):
    obj["avatar_url"] = "gravatar" if visible and gravatar else "masked"
""",
    ).body[0]
    cls.body = [
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "finalize_payload"
    ] + [avatar]
    ast.fix_missing_locations(cls)
    module = ModuleType("message_cache")
    vars(module).update(
        copy=copy,
        Address=Address,
        Any=object,
        Message=SimpleNamespace(EMPTY_TOPIC_FALLBACK_NAME="general chat"),
        Recipient=SimpleNamespace(STREAM=2),
        UserProfile=SimpleNamespace(
            EMAIL_ADDRESS_VISIBILITY_EVERYONE=1, INACCESSIBLE_USER_NAME="Unknown user"
        ),
        get_fake_email_domain=lambda host: "hidden." + host,
        native_message_payload=bridge,
        reply_links=replies,
        plain_reply_link_fields=replies.plain_reply_link_fields,
        _REFERENCE_REPLY_FIELDS=replies.plain_reply_link_fields,
    )
    # The only compiled code is the selected functions from the checked-out file.
    exec(compile(ast.Module([*outer, cls], []), str(path), "exec"), vars(module))  # noqa: S102
    return module


def make_payload(
    obj: dict[str, object],
    *,
    markdown: bool = True,
    empty_topic: bool = False,
    keep: bool = False,
    same_dict: bool = False,
    accessible: bool = True,
    mobile: bool = True,
) -> dict[str, object]:
    return {
        "obj": obj,
        "apply_markdown": markdown,
        "client_gravatar": True,
        "allow_empty_topic_name": empty_topic,
        "keep_rendered_content": keep,
        "skip_copy": same_dict,
        "can_access_sender": accessible,
        "realm_host": "example.test",
        "plain_reply_links": mobile,
    }


def example() -> dict[str, object]:
    return {
        "content": "Raw Markdown",
        "match_content": REPLY,
        "rendered_content": REPLY,
        "recipient_type": 2,
        "recipient_type_id": 1,
        "subject": "",
        "sender_id": 12,
        "sender_email": "synthetic@public.example.test",
        "sender_full_name": "Synthetic sender",
        "sender_realm_id": 5,
        "sender_avatar_source": "G",
        "sender_delivery_email": "synthetic@delivery.example.test",
        "sender_avatar_version": 3,
        "sender_is_mirror_dummy": False,
        "sender_email_address_visibility": 1,
        "can_access_sender": False,
        "edit_history": [
            {
                "prev_topic": "",
                "topic": "new",
                "prev_rendered_content_version": 31,
                "prev_rendered_content": REPLY,
            }
        ],
    }


class MessagePayloadParityTest(unittest.TestCase):
    bridge: ClassVar[ModuleType]
    reference: ClassVar[ModuleType]
    mobile_python: ClassVar[ModuleType]
    native: ClassVar[ModuleType]
    extension: ClassVar[ModuleType]

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.bridge = load_bridge(enabled=True)
        cls.reference = load_cache(load_bridge(enabled=False), load_reply_links(enabled=False))
        cls.mobile_python = load_cache(cls.bridge, load_reply_links(enabled=False))
        cls.native = load_cache(cls.bridge, load_reply_links(enabled=True))
        cls.extension = import_module("_zulip_backend")

    def test_shapes_copying_and_no_redaction_change(self) -> None:
        for markdown, allowed, keep, same, accessible, mobile in itertools.product(
            (False, True), repeat=6
        ):
            with self.subTest(markdown=markdown, allowed=allowed, mobile=mobile, same=same):
                template = example()
                for cache in (self.mobile_python, self.native):
                    left, right = copy.deepcopy(template), copy.deepcopy(template)
                    args = dict(
                        markdown=markdown,
                        empty_topic=allowed,
                        keep=keep,
                        same_dict=same,
                        accessible=accessible,
                        mobile=mobile,
                    )
                    original_history = right["edit_history"]
                    expected = self.reference.MessageDict.finalize_payload(
                        **make_payload(left, **args)
                    )
                    result = cache.MessageDict.finalize_payload(**make_payload(right, **args))
                    self.assertEqual(list(result.items()), list(expected.items()))
                    self.assertEqual(left, right)
                    self.assertIs(result is right, same)
                    self.assertIs(result["edit_history"] is original_history, not mobile)
                    self.assertEqual(
                        result["content_type"], "text/html" if markdown else "text/x-markdown"
                    )
                    self.assertEqual(result["match_content"], PLAIN if mobile else REPLY)
                    self.assertNotIn("sender_delivery_email", result)
                    if not accessible:
                        self.assertEqual(result["sender_email"], "user12@hidden.example.test")
                        self.assertEqual(result["sender_full_name"], "Unknown user")

    def test_annotation_order_duplicate_ids_and_missing_targets(self) -> None:
        big = 2**150
        messages = [
            {"id": 1, "content": "first duplicate"},
            {"id": big, "content": "second message"},
            {"id": 1, "content": "last duplicate"},
        ]
        annotations = [
            {"message_id": big, "data": "first"},
            {"message_id": 1, "data": "second"},
            {"message_id": 1, "data": "third"},
        ]
        result = self.native.sew_messages_and_reactions(messages, annotations)
        self.assertIs(result[0], messages[-1])
        self.assertIs(result[1], messages[1])
        self.assertEqual(messages[0]["reactions"], [])
        self.assertIs(result[0]["reactions"][0], annotations[1])
        self.assertIs(result[0]["reactions"][1], annotations[2])
        with_orphan = [*annotations, {"message_id": 2, "data": "orphan"}]
        self.native.sew_messages_and_submessages(messages, with_orphan)
        self.assertEqual(messages[0]["submessages"], [])
        self.assertEqual(messages[-1]["submessages"], annotations[1:])
        invalid = [{"id": 1}, {"id": 2}]
        self.assertIsNone(self.extension.assemble_message_reactions(invalid, with_orphan))
        self.assertEqual(invalid, [{"id": 1}, {"id": 2}])
        with self.assertRaises(KeyError):
            self.native.sew_messages_and_reactions(invalid, with_orphan)
        self.assertEqual(invalid[0]["reactions"], [])

    def test_seeded_annotations(self) -> None:
        rng = random.Random(528219)
        for _ in range(1000):
            messages = [{"id": rng.randrange(6)} for _ in range(rng.randrange(12))]
            ids = [message["id"] for message in messages]
            reactions = (
                [
                    {"message_id": rng.choice(ids), "data": sequence}
                    for sequence in range(rng.randrange(12))
                ]
                if ids
                else []
            )
            submessages = [*reactions, {"message_id": 90, "data": "missing"}]
            other = copy.deepcopy(messages)
            expected = self.reference.sew_messages_and_reactions(messages, reactions)
            actual = self.native.sew_messages_and_reactions(other, reactions)
            self.assertEqual(actual, expected)
            self.reference.sew_messages_and_submessages(messages, submessages)
            self.native.sew_messages_and_submessages(other, submessages)
            self.assertEqual(other, messages)

    def test_malformed_rows_fall_back_before_mutation_and_callback_errors_do_not_retry(
        self,
    ) -> None:
        obj = example()
        del obj["rendered_content"]
        before = copy.deepcopy(obj)
        self.assertFalse(
            self.extension.finalize_message_payload_content(obj, True, False, "legacy", None)
        )
        self.assertEqual(obj, before)
        with self.assertRaises(KeyError):
            self.native.MessageDict.finalize_payload(**make_payload(obj, same_dict=True))
        self.assertEqual(obj["content_type"], "text/html")

        obj = example()
        calls = []

        def failure(*args: object) -> object:
            calls.append(args)
            raise RuntimeError("callback failed")

        with self.assertRaisesRegex(RuntimeError, "callback failed"):
            self.extension.finalize_message_payload_content(obj, True, False, "legacy", failure)
        self.assertEqual(calls, [ANY])
        self.assertEqual(obj["content"], obj["rendered_content"])
        self.assertEqual(obj["content_type"], "text/html")

    def test_narrow_projection_preserves_order_identity_and_declines_without_mutation(self) -> None:
        obj = example()
        history = cast(list[dict[str, object]], obj["edit_history"])
        history[0].update(user_id=8, prev_content="Authorized earlier Markdown")
        # The real Python finalizer owns the surrounding card and never applies
        # additions from outside the closed content contract.
        obj["unrelated"] = marker = object()
        row = history[0]

        real_finalize = self.extension.finalize_message_payload_content

        def finalize(payload: dict[str, object], *args: object) -> bool:
            self.assertEqual(set(payload), {"rendered_content", "edit_history"})
            projected = cast(list[dict[str, object]], payload["edit_history"])
            self.assertEqual(projected[0], {"prev_rendered_content_version": None})
            self.assertIsNot(projected, history)
            self.assertIsNot(projected[0], row)
            result = real_finalize(payload, *args)
            payload["unrelated"] = "not a content output"
            projected[0]["user_id"] = "not an edit output"
            return result

        def decline(payload: dict[str, object], *args: object) -> bool:
            payload["content_type"] = "ignore transient content"
            cast(list[dict[str, object]], payload["edit_history"])[0].clear()
            return False

        def run() -> bool:
            return self.bridge.finalize_content(
                obj,
                apply_markdown=True,
                allow_empty_topic=True,
                empty_topic_fallback="general chat",
                plain_reply_fields=None,
            )

        before = list(obj.items())
        with patch.object(self.extension, "finalize_message_payload_content", side_effect=decline):
            self.assertFalse(run())
        self.assertEqual(list(obj.items()), before)
        self.assertEqual(row["prev_rendered_content_version"], 31)
        with patch.object(self.extension, "finalize_message_payload_content", side_effect=finalize):
            self.assertTrue(run())
        self.assertEqual(obj["content"], REPLY)
        self.assertEqual(list(obj)[-1], "content_type")
        self.assertIs(obj["unrelated"], marker)
        self.assertIs(obj["edit_history"], history)
        self.assertIs(history[0], row)
        self.assertEqual(row["user_id"], 8)
        self.assertEqual(row["prev_content"], "Authorized earlier Markdown")
        self.assertNotIn("prev_rendered_content_version", row)

    def test_projected_callback_error_preserves_partial_assignments_without_retry(self) -> None:
        obj = example()
        history = cast(list[dict[str, object]], obj["edit_history"])
        calls = []

        def failure(payload: object, *fields: str) -> dict[str, str]:
            calls.append(fields)
            if fields == ("prev_rendered_content",):
                raise RuntimeError("callback failed during history")
            return {"match_content": PLAIN}

        with self.assertRaisesRegex(RuntimeError, "callback failed during history"):
            self.bridge.finalize_content(
                obj,
                apply_markdown=True,
                allow_empty_topic=False,
                empty_topic_fallback="general chat",
                plain_reply_fields=failure,
            )
        self.assertEqual(calls, [("content", "match_content"), ("prev_rendered_content",)])
        self.assertEqual(obj["content"], REPLY)
        self.assertEqual(obj["content_type"], "text/html")
        self.assertEqual(obj["match_content"], PLAIN)
        self.assertIs(obj["edit_history"], history)
        self.assertEqual(history[0]["prev_rendered_content_version"], 31)

    def test_lazy_values_evaluate_after_initialization_and_raise_once(self) -> None:
        messages = [{"id": 1}, {"id": 2}]
        calls = []

        def rows() -> Iterator[dict[str, int]]:
            calls.append("iterated")
            self.assertEqual(messages, [{"id": 1, "reactions": []}, {"id": 2, "reactions": []}])
            yield {"message_id": 1}
            yield {"message_id": 3}

        with self.assertRaises(KeyError) as error:
            self.extension.assemble_message_reactions(messages, rows(), True)
        self.assertEqual(error.exception.args, (3,))
        self.assertEqual(calls, ["iterated"])
        self.assertEqual(messages[0]["reactions"], [{"message_id": 1}])
        self.assertEqual(messages[1]["reactions"], [])

    def test_nonstandard_overwrites_use_the_reference(self) -> None:
        obj = example()
        obj["content_type"] = object()
        self.assertFalse(
            self.extension.finalize_message_payload_content(obj, True, False, "legacy", None)
        )
        output = self.native.MessageDict.finalize_payload(**make_payload(obj))
        self.assertEqual(output["content_type"], "text/html")
        messages = [{"id": 1, "reactions": [{"old": "value"}]}]
        self.assertIsNone(self.extension.assemble_message_reactions(messages, []))
        self.assertEqual(messages, [{"id": 1, "reactions": [{"old": "value"}]}])
        self.assertEqual(
            self.native.sew_messages_and_reactions(messages, []), [{"id": 1, "reactions": []}]
        )

    def test_export_dispatch_replaced_reply_callback_and_old_wheel(self) -> None:
        extension = self.extension
        with (
            patch.object(
                extension,
                "finalize_message_payload_content",
                wraps=extension.finalize_message_payload_content,
            ) as finalize,
            patch.object(
                extension, "assemble_message_reactions", wraps=extension.assemble_message_reactions
            ) as reactions,
            patch.object(
                extension,
                "assemble_message_submessages",
                wraps=extension.assemble_message_submessages,
            ) as submessages,
        ):
            self.native.MessageDict.finalize_payload(**make_payload(example()))
            self.native.sew_messages_and_reactions([{"id": 1}], [])
            self.native.sew_messages_and_submessages([{"id": 1}], [])
            finalize.assert_called_once()
            reactions.assert_called_once()
            submessages.assert_called_once()
            with patch.object(self.native, "plain_reply_link_fields", return_value={}):
                obj = self.native.MessageDict.finalize_payload(**make_payload(example()))
                self.assertEqual(obj["content"], REPLY)
            finalize.assert_called_once()
        with patch.object(self.bridge, "_backend", ModuleType("old_backend")):
            result = self.native.MessageDict.finalize_payload(**make_payload(example()))
            self.assertEqual(result["content"], PLAIN)

    def test_seeded_differential(self) -> None:
        rng = random.Random(321097)
        for _ in range(4000):
            obj = example()
            if rng.randrange(5):
                obj["edit_history"] = [
                    {
                        "topic": rng.choice(("", "Topic", "فارسی", "\ud800")),
                        "prev_topic": rng.choice(("", "Prior", "🧪")),
                        "prev_rendered_content": rng.choice((None, REPLY, " ", REPLY + "\ud800")),
                        "prev_rendered_content_version": rng.randrange(60),
                    }
                    for _ in range(rng.randrange(6))
                ]
            else:
                del obj["edit_history"]
            if rng.getrandbits(1):
                obj["recipient_type"] = 3
            other = copy.deepcopy(obj)
            args = dict(
                markdown=bool(rng.getrandbits(1)),
                empty_topic=bool(rng.getrandbits(1)),
                keep=bool(rng.getrandbits(1)),
                same_dict=bool(rng.getrandbits(1)),
                accessible=bool(rng.getrandbits(1)),
                mobile=bool(rng.getrandbits(1)),
            )
            expected = self.reference.MessageDict.finalize_payload(**make_payload(obj, **args))
            actual = self.native.MessageDict.finalize_payload(**make_payload(other, **args))
            self.assertEqual(list(actual.items()), list(expected.items()))
            self.assertEqual(other, obj)


if __name__ == "__main__":
    unittest.main()
