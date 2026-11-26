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
