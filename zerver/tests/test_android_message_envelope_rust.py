from datetime import datetime, timezone
from types import ModuleType
from unittest.mock import patch

import orjson
from django.test import SimpleTestCase

from zerver.lib import native_push_text, push_notifications
from zerver.lib.push_notifications import get_message_payload_gcm
from zerver.models import Message, Realm, UserProfile


class AndroidMessageEnvelopeRustTest(SimpleTestCase):
    def message(self) -> tuple[UserProfile, Message]:
        realm = Realm(string_id="synthetic")
        sender = UserProfile(realm=realm, full_name="Visible Name")
        message = Message(
            id=403,
            realm=realm,
            sender=sender,
            rendered_content=f"<p>{'🙂' * 204}</p>",
            date_sent=datetime(2022, 8, 1, 12, tzinfo=timezone.utc),
        )
        return UserProfile(realm=realm, default_language="fa"), message

    def test_both_modes_keep_visible_fields_redaction_and_order(self) -> None:
        actor, message = self.message()
        original = {"start": ["value"], "time": -1, "content": "replace", "finish": 8}
        for legacy in (True, False):
            for visible in (True, False):
                with (
                    self.subTest(legacy=legacy, visible=visible),
                    patch.object(
                        push_notifications,
                        "absolute_avatar_url",
                        return_value="https://synthetic.invalid/visible",
                    ) as visible_avatar,
                    patch.object(
                        push_notifications,
                        "get_avatar_for_inaccessible_user",
                        return_value="https://synthetic.invalid/hidden",
                    ) as hidden_avatar,
                ):
                    with patch.object(
                        native_push_text, "_android_message_envelope_backend", ModuleType("old")
                    ):
                        reference = get_message_payload_gcm(
                            original, actor, message, visible, legacy
                        )
                    payload, options = get_message_payload_gcm(
                        original, actor, message, visible, legacy
                    )
                    self.assertEqual(orjson.dumps((payload, options)), orjson.dumps(reference))
                    self.assertEqual(options, {"priority": "high"})
                    self.assertEqual(payload["content"], "🙂" * 200 + "…")
                    self.assertEqual(payload["time"], 1659355200)
                    identity = ("event", "zulip_message_id") if legacy else ("type", "message_id")
                    self.assertEqual(payload[identity[0]], "message")
                    self.assertEqual(payload[identity[1]], 403)
                    self.assertEqual(
                        tuple(payload),
                        (*original, *identity, "sender_full_name", "sender_avatar_url"),
                    )
                    self.assertIsNot(payload["start"], original["start"])
                    if visible:
                        self.assertEqual(payload["sender_full_name"], message.sender.full_name)
                        self.assertEqual(visible_avatar.call_count, 2)
                        hidden_avatar.assert_not_called()
                    else:
                        self.assertNotEqual(payload["sender_full_name"], message.sender.full_name)
                        self.assertEqual(
                            payload["sender_avatar_url"], "https://synthetic.invalid/hidden"
                        )
                        visible_avatar.assert_not_called()
                        self.assertEqual(hidden_avatar.call_count, 2)
        self.assertEqual(
            original, {"start": ["value"], "time": -1, "content": "replace", "finish": 8}
        )

    def test_current_extension_receives_only_prepared_values(self) -> None:
        native = native_push_text._android_message_envelope_backend
        if native is None:
            self.skipTest("requires current native Android envelope")
        actor, message = self.message()
        with (
            patch.object(
                push_notifications,
                "absolute_avatar_url",
                return_value="https://synthetic.invalid/visible",
            ),
            patch.object(
                native,
                "format_prepared_android_message",
                wraps=native.format_prepared_android_message,
            ) as assemble,
        ):
            payload, options = get_message_payload_gcm({}, actor, message, for_legacy_clients=False)
        self.assertEqual(options, {"priority": "high"})
        assemble.assert_called_once_with(
            payload,
            1659355200,
            "🙂" * 200 + "…",
            "Visible Name",
            "https://synthetic.invalid/visible",
        )
