from unittest.mock import Mock

from django.test import SimpleTestCase

from zerver.lib import native_push_text


class PushDirectRecipientIDsRustTest(SimpleTestCase):
    def test_ids_sorted_deduplicated_and_sender_loaded_only_for_one_id(self) -> None:
        backend = native_push_text._direct_recipients_backend
        if backend is None:
            self.skipTest("No prepared Rust mobile recipient ABI")
        gather = backend.format_mobile_direct_recipient_ids
        sender = Mock(return_value=87)
        self.assertEqual(gather([], sender), [])
        self.assertEqual(gather([{"id": 32}, {"id": 11}, {"id": 32}], sender), [11, 32])
        sender.assert_not_called()
        self.assertEqual(gather([{"id": 32}, {"id": 32}], sender), [32, 87])
        sender.assert_called_once_with()

    def test_unusual_values_keep_existing_behavior_without_retry(self) -> None:
        backend = native_push_text._direct_recipients_backend
        if backend is None:
            self.skipTest("No prepared Rust mobile recipient ABI")
        gather = backend.format_mobile_direct_recipient_ids
        sender = Mock(return_value=2**80)
        self.assertIsNone(gather([{"id": 2**80}], sender))
        sender.assert_not_called()
        self.assertEqual(gather([{"id": 3}], sender), [3, 2**80])
        sender.assert_called_once_with()
