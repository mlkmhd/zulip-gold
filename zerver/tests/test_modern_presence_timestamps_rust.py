from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from zerver.lib import native_presence, presence, timestamp


class ModernPresenceTimestampsContractTest(SimpleTestCase):
    def test_modern_timestamp_shape_near_epoch_and_extreme_calendar_years(self) -> None:
        for active in (
            datetime(1969, 12, 31, 23, 59, 59, 999_999, timezone.utc),
            datetime(2000, 2, 29, 1, 2, 3, 999_999, timezone.utc),
            datetime(9999, 12, 31, 23, 59, 58, 999_999, timezone.utc),
            datetime(2026, 1, 1, 0, 0, 0, 1, ZoneInfo("UTC")),
        ):
            connected = active + timedelta(milliseconds=2)
            with self.subTest(active=active):
                self.assertEqual(
                    presence.get_modern_user_presence_info(active, connected),
                    {
                        "active_timestamp": timestamp.datetime_to_timestamp(active),
                        "idle_timestamp": timestamp.datetime_to_timestamp(connected),
                    },
                )

    def test_modern_native_converter_does_not_enable_batch_or_legacy_flows(self) -> None:
        backend = native_presence._modern_backend
        if getattr(backend, "PRESENCE_MODERN_TIMESTAMPS_API", None) != 1:
            self.skipTest("Matching Rust modern presence timestamp contract is not available")
        active = datetime(2026, 1, 1, 8, 1, tzinfo=timezone.utc)
        connected = active + timedelta(seconds=17)
        with (
            patch.object(native_presence, "_backend", None),
            patch.object(native_presence, "_rows_backend", None),
            patch.object(
                backend,
                "format_modern_presence_timestamps",
                wraps=backend.format_modern_presence_timestamps,
            ) as native,
        ):
            self.assertFalse(native_presence.is_enabled())
            self.assertIsNone(native_presence.format_rows([], None))
            self.assertEqual(
                presence.get_modern_user_presence_info(active, connected),
                {
                    "active_timestamp": int(active.timestamp()),
                    "idle_timestamp": int(connected.timestamp()),
                },
            )
            self.assertIn("aggregated", presence.get_legacy_user_presence_info(active, connected))
        native.assert_called_once()

    def test_custom_validator_still_has_its_original_calls(self) -> None:
        active = datetime(2026, 1, 1, tzinfo=timezone.utc)
        connected = active + timedelta(seconds=17)
        with patch.object(timestamp, "verify_UTC", wraps=timestamp.verify_UTC) as verify:
            presence.get_modern_user_presence_info(active, connected)
        self.assertEqual([call.args[0] for call in verify.call_args_list], [active, connected])
