import json
from unittest.mock import patch

from zerver.lib import native_push_text, push_notifications
from zerver.lib.test_classes import ZulipTestCase


class PushRemovePayloadRustTest(ZulipTestCase):
    def test_payloads_match_order_bytes_and_modern_list_identity(self) -> None:
        backend = native_push_text._remove_payload_backend
        if backend is None:
            self.skipTest("No prepared Rust removal payload ABI")
        user_profile = self.example_user("hamlet")
        for ids in ([], [19, 2, 19, -4, 2**63 - 1], [19, 2**80]):
            with (
                self.subTest(ids=ids),
                patch.object(push_notifications, "get_apns_badge_count", return_value=17),
            ):

                def build(
                    ids: list[int],
                ) -> tuple[
                    tuple[dict[str, object], dict[str, object]],
                    dict[str, object],
                    dict[str, object],
                ]:
                    return (
                        push_notifications.get_remove_payload_gcm(user_profile, ids),
                        push_notifications.get_remove_payload_apns(user_profile, ids),
                        push_notifications.get_remove_payload_data_to_encrypt(user_profile, ids),
                    )

                with patch.object(native_push_text, "_remove_payload_backend", None):
                    expected = build(ids)
                with patch.object(
                    backend,
                    "format_mobile_remove_fields",
                    wraps=backend.format_mobile_remove_fields,
                ) as prepare:
                    actual = build(ids)
                self.assertEqual(prepare.call_count, 3)
                self.assertEqual(json.dumps(actual), json.dumps(expected))
                self.assertIs(actual[2]["message_ids"], ids)

    def test_unsupported_legacy_input_does_not_mutate_payload_on_decline(self) -> None:
        backend = native_push_text._remove_payload_backend
        if backend is None:
            self.skipTest("No prepared Rust removal payload ABI")
        payload = {"existing": "same"}
        ids = [1, 2**80]
        self.assertFalse(backend.format_mobile_remove_fields(payload, ids, True))
        self.assertEqual(payload, {"existing": "same"})
