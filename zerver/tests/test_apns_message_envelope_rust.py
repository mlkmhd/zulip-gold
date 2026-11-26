from types import ModuleType
from unittest.mock import patch

import orjson
from django.test import SimpleTestCase
from django.utils.translation import get_language

from zerver.lib import native_push_text, push_notifications
from zerver.lib.push_notifications import get_message_payload_apns
from zerver.models import Message, Recipient, UserProfile


class APNSMessageEnvelopeRustTest(SimpleTestCase):
    def messages(self) -> tuple[UserProfile, Message]:
        sender = UserProfile(full_name="Synthetic Sender")
        message = Message(
            id=392,
            sender=sender,
            recipient=Recipient(type=Recipient.STREAM),
            is_channel_message=True,
            rendered_content="<p>موضوع 🙂</p>",
        )
        return UserProfile(default_language="fr"), message

    def test_wire_bytes_and_original_input_remain_unchanged(self) -> None:
        user, message = self.messages()
        shared = {
            "server": "synthetic.invalid",
            "realm_id": 8,
            "topic": "موضوع",
            "stream": "کانال",
            "items": [1, 2],
        }
        for badge_count in (0, 7, 2**30):
            with patch.object(
                push_notifications, "get_apns_badge_count", return_value=badge_count
            ) as badge:
                with patch.object(
                    native_push_text, "_apns_message_envelope_backend", ModuleType("old")
                ):
                    old = get_message_payload_apns(shared, user, message, "ordinary")
                badge.reset_mock()
                actual = get_message_payload_apns(shared, user, message, "ordinary")
                badge.assert_called_once_with(user)
            self.assertEqual(orjson.dumps(actual), orjson.dumps(old))
            self.assertEqual(tuple(actual), ("alert", "sound", "badge", "custom"))
            self.assertEqual(tuple(actual["alert"]), ("title", "subtitle", "body"))
            self.assertEqual(actual["badge"], badge_count)
            inner = actual["custom"]["zulip"]
            self.assertEqual(inner["message_ids"], [392])
            self.assertNotIn("realm_id", inner)
            self.assertNotIn("server", inner)
            self.assertIsNot(inner["items"], shared["items"])
        self.assertEqual(shared["realm_id"], 8)
        self.assertNotIn("message_ids", shared)

    def test_subtitle_and_badge_order_inside_locale_and_no_retry(self) -> None:
        user, message = self.messages()
        shared = {"server": "synthetic.invalid", "realm_id": 8, "stream": "C", "topic": "topic"}
        seen = []

        def subtitle(*args: object) -> str:
            seen.append(("subtitle", get_language()))
            return "prepared subtitle"

        def badge(*args: object) -> int:
            seen.append(("badge", get_language()))
            raise ValueError("synthetic badge unavailable")

        with (
            patch.object(push_notifications, "get_apns_alert_subtitle", side_effect=subtitle),
            patch.object(push_notifications, "get_apns_badge_count", side_effect=badge),
            self.assertRaisesRegex(ValueError, "^synthetic badge unavailable$"),
        ):
            get_message_payload_apns(shared, user, message, "ordinary")
        self.assertEqual(seen, [("subtitle", "fr"), ("badge", "fr")])

    def test_current_extension_sees_only_prepared_fields(self) -> None:
        backend = native_push_text._apns_message_envelope_backend
        if backend is None:
            self.skipTest("requires current native APNS message envelope")
        user, message = self.messages()
        with (
            patch.object(push_notifications, "get_apns_badge_count", return_value=6),
            patch.object(
                backend,
                "assemble_prepared_apns_message",
                wraps=backend.assemble_prepared_apns_message,
            ) as call,
        ):
            result = get_message_payload_apns(
                {"server": "synthetic.invalid", "realm_id": 8, "stream": "C", "topic": "topic"},
                user,
                message,
                "ordinary",
            )
        call.assert_called_once_with(
            "#C > topic", "Synthetic Sender:", "موضوع 🙂", 6, result["custom"]["zulip"]
        )
