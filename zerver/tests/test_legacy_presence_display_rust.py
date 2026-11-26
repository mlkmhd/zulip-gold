from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, override_settings

from zerver.lib import native_presence, presence
from zerver.models import UserPresence


class LegacyPresenceDisplayContractTest(SimpleTestCase):
    @override_settings(PRESENCE_LEGACY_EVENT_OFFSET_FOR_ACTIVITY_SECONDS=3)
    def test_legacy_active_cutoff_and_payload_flags(self) -> None:
        for tz in (timezone.utc, ZoneInfo("UTC")):
            active = datetime(1969, 12, 31, 23, 59, 59, 999_999, tz)
            for delay, status in (
                (timedelta(seconds=3), UserPresence.LEGACY_STATUS_ACTIVE),
                (timedelta(seconds=3, microseconds=1), UserPresence.LEGACY_STATUS_IDLE),
            ):
                connected = active + delay
                result = presence.get_legacy_user_presence_info(active, connected)
                self.assertEqual(result["website"]["status"], status)
                self.assertFalse(result["website"]["pushable"])
                self.assertNotIn("pushable", result["aggregated"])
                self.assertEqual(result["aggregated"]["status"], status)
                self.assertEqual(
                    result["website"], presence.format_legacy_presence_dict(active, connected)
                )

    def test_legacy_contract_does_not_enable_native_batch(self) -> None:
        backend = native_presence._legacy_display_backend
        if getattr(backend, "PRESENCE_LEGACY_DISPLAY_API", None) != 1:
            self.skipTest("Matching Rust legacy presence display contract is not available")
        active = datetime(2026, 1, 1, tzinfo=timezone.utc)
        connected = active + timedelta(seconds=30)
        with (
            patch.object(native_presence, "_backend", None),
            patch.object(native_presence, "_legacy_display_backend", None),
        ):
            expected = presence.get_legacy_user_presence_info(active, connected)
        with (
            patch.object(native_presence, "_backend", None),
            patch.object(
                backend, "format_presence_information", wraps=backend.format_presence_information
            ) as native,
        ):
            self.assertFalse(native_presence.is_enabled())
            self.assertIsNone(native_presence.format_rows([], None))
            self.assertEqual(presence.get_legacy_user_presence_info(active, connected), expected)
        native.assert_called_once()
        self.assertIs(native.call_args.args[-1], True)
