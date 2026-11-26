import json
from unittest.mock import patch

from zerver.lib import native_push_text, push_notifications
from zerver.lib.test_classes import ZulipTestCase


class PushMessageEnvelopeRustTest(ZulipTestCase):
    def test_shared_mobile_payload_matches_order_and_bytes(self) -> None:
        backend = native_push_text._base_payload_backend
        if backend is None:
            self.skipTest("No prepared Rust common mobile payload ABI")
        user = self.example_user("hamlet")
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                with patch.object(native_push_text, "_base_payload_backend", None):
                    expected = push_notifications.get_base_payload(user, legacy)
                with patch.object(
                    backend, "format_mobile_base_payload", wraps=backend.format_mobile_base_payload
                ) as native:
                    actual = push_notifications.get_base_payload(user, legacy)
                native.assert_called_once()
                self.assertEqual(json.dumps(actual), json.dumps(expected))
                self.assertEqual(
                    tuple(actual),
                    ("server", "realm_id", "realm_uri", "realm_url", "realm_name", "user_id")
                    if legacy
                    else ("realm_url", "realm_name", "user_id"),
                )

    def test_platform_identity_envelopes_keep_key_order_and_original_id(self) -> None:
        if native_push_text._message_identity_backend is None:
            self.skipTest("No prepared Rust message identity ABI")
        message_id = 10**35
        apns_payload: dict[str, object] = {"existing": "one"}
        legacy_payload: dict[str, object] = {"existing": "two"}
        modern_payload: dict[str, object] = {"existing": "three"}
        self.assertTrue(native_push_text.format_message_identity(apns_payload, message_id, "apns"))
        self.assertTrue(
            native_push_text.format_message_identity(legacy_payload, message_id, "legacy")
        )
        self.assertTrue(
            native_push_text.format_message_identity(modern_payload, message_id, "modern")
        )
        self.assertEqual(
            list(apns_payload.items()), [("existing", "one"), ("message_ids", [message_id])]
        )
        self.assertEqual(
            list(legacy_payload.items()),
            [("existing", "two"), ("event", "message"), ("zulip_message_id", message_id)],
        )
        self.assertEqual(
            list(modern_payload.items()),
            [("existing", "three"), ("type", "message"), ("message_id", message_id)],
        )
        values = apns_payload["message_ids"]
        assert isinstance(values, list)
        self.assertIs(values[0], message_id)
        self.assertIs(legacy_payload["zulip_message_id"], message_id)
        self.assertIs(modern_payload["message_id"], message_id)
