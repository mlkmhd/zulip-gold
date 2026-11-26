from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import native_push_text


class PushChannelRecipientRustTest(SimpleTestCase):
    def test_channel_recipient_keeps_both_wire_shapes_and_original_values(self) -> None:
        backend = native_push_text._channel_recipient_backend
        if backend is None:
            self.skipTest("No prepared Rust mobile channel recipient ABI")
        display = "فارسی \ud800 🙂"
        channel_id = 2**80
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                payload: dict[str, object] = {"existing": "same"}
                native = backend.format_mobile_channel_recipient
                with patch.object(
                    backend, "format_mobile_channel_recipient", wraps=native
                ) as format:
                    self.assertTrue(
                        native_push_text.format_channel_recipient(
                            payload, channel_id, display, legacy
                        )
                    )
                format.assert_called_once()
                names = ("stream", "stream_id") if legacy else ("channel_name", "channel_id")
                self.assertEqual(tuple(payload), ("existing", "recipient_type", *names))
                self.assertEqual(payload["recipient_type"], "stream" if legacy else "channel")
                self.assertIs(payload[names[0]], display)
                self.assertIs(payload[names[1]], channel_id)
