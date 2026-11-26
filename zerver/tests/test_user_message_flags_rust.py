from importlib import import_module
from random import Random
from types import ModuleType, SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson

from zerver.actions.message_flags import do_update_message_flags
from zerver.actions.message_send import do_send_messages, internal_prep_private_message
from zerver.lib import native_user_message_flags
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.user_message import UserMessageLite
from zerver.models import UserMessage


def extension() -> ModuleType:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(backend, "label_user_message_flags", None)):
        raise SkipTest("Rust extension does not support message flag labels")
    return backend


class UserMessageFlagsRustTest(ZulipTestCase):
    def test_label_order_and_non_api_bits_match_django(self) -> None:
        backend = extension()
        random = Random(2009)
        masks = [0, *(1 << bit for bit in range(64)), 2**64 - 1]
        masks.extend(random.getrandbits(64) for _ in range(768))
        pairs = list(enumerate(masks))
        pairs += [(3, 1), (2**100, masks[-1])]
        reference = {key: UserMessage.flags_list_for_flags(mask) for key, mask in pairs}
        with patch.object(native_user_message_flags, "_backend", backend):
            native = native_user_message_flags.labels_by_id(iter(pairs))
            self.assertEqual(native, reference)
            self.assertEqual(list(native or {}), list(reference))
            self.assertIsNone(native_user_message_flags.labels_by_id([(1, 0), (2, -1)]))
            self.assertIsNone(native_user_message_flags.labels_by_id([(1, 0), (2, 2**64)]))
        self.assertTrue(
            all(not UserMessage.NON_API_FLAGS.intersection(entry) for entry in reference.values())
        )

    def test_prepared_send_keeps_delivery_users_and_public_labels(self) -> None:
        prepared = native_user_message_flags._prepared_send_backend
        if prepared is None:
            self.skipTest("No prepared send-event label ABI")
        sender = self.example_user("hamlet")
        recipient = self.example_user("cordelia")

        def send() -> bytes:
            prepared_message = internal_prep_private_message(
                sender, recipient, "Public flag label sample"
            )
            with self.capture_send_event_calls(expected_num_events=1) as events:
                do_send_messages([prepared_message])
            self.assertEqual(events[0]["event"]["type"], "message")
            return orjson.dumps(events[0]["users"])

        with (
            patch.object(native_user_message_flags, "_backend", None),
            patch.object(native_user_message_flags, "_prepared_send_backend", None),
        ):
            expected = send()

        with (
            patch.object(native_user_message_flags, "_backend", None),
            patch.object(
                prepared, "label_user_message_flags", wraps=prepared.label_user_message_flags
            ) as witness,
        ):
            self.assertEqual(send(), expected)
            witness.assert_called_once()
            row = UserMessageLite(sender.id, 1, 0)
            with patch.object(UserMessageLite, "flags_list", return_value=["custom"]):
                self.assertIsNone(native_user_message_flags.labels_for_prepared_send([row]))
            witness.assert_called_once()

    def test_single_fallback_preserves_owned_and_historical_message_flags(self) -> None:
        backend = extension()
        owner = self.example_user("hamlet")
        peer = self.example_user("cordelia")
        delivery = self.send_personal_message(peer, owner, "Test receipt labels")
        do_update_message_flags(owner, "add", "starred", [delivery])
        own = self.subscribe(owner, "Receipt labels")
        self.subscribe(peer, own.name)
        historical = self.send_stream_message(peer, own.name, topic_name="Test topic")
        UserMessage.objects.filter(user_profile=owner, message_id=historical).delete()
        current = self.send_stream_message(peer, own.name, topic_name="Recent topic")
        do_update_message_flags(owner, "add", "starred", [current])

        cases = (
            (
                [{"operator": "is", "operand": "dm"}],
                delivery,
                "starred",
            ),
            (
                [{"operator": "channel", "operand": own.name}],
                historical,
                "historical",
            ),
        )
        for narrow, expected_id, flag in cases:
            data = {
                "anchor": "newest",
                "num_before": 200,
                "num_after": 0,
                "narrow": orjson.dumps(narrow).decode(),
            }
            with (
                self.subTest(flag=flag),
                patch.object(native_user_message_flags, "_backend", None),
                patch.object(native_user_message_flags, "_prepared_fetch_backend", None),
            ):
                response = self.api_get(owner, "/api/v1/messages", data)
                ref = self.assert_json_success(response)
                ref_bytes = response.content
            self.assertIn(
                flag, next(row["flags"] for row in ref["messages"] if row["id"] == expected_id)
            )
            prepared = native_user_message_flags._prepared_fetch_backend
            if prepared is not None:
                with (
                    patch.object(native_user_message_flags, "_backend", None),
                    patch.object(
                        prepared,
                        "label_user_message_flags",
                        wraps=prepared.label_user_message_flags,
                    ) as prepared_witness,
                ):
                    self.assertEqual(
                        self.api_get(owner, "/api/v1/messages", data).content, ref_bytes
                    )
                prepared_witness.assert_called_once()
                with (
                    patch.object(native_user_message_flags, "_backend", None),
                    patch.object(UserMessage, "flags_list", Mock()),
                ):
                    self.assertFalse(native_user_message_flags.available(prepared_fetch=True))

            witness = Mock(wraps=backend.label_user_message_flags)
            decline = Mock(return_value=None)
            for candidate in (
                SimpleNamespace(label_user_message_flags=witness),
                SimpleNamespace(),
                SimpleNamespace(label_user_message_flags=decline),
            ):
                with patch.object(native_user_message_flags, "_backend", candidate):
                    self.assertEqual(
                        self.api_get(owner, "/api/v1/messages", data).content, ref_bytes
                    )
            witness.assert_called_once()
            decline.assert_called_once()
