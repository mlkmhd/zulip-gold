from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from zerver.lib import native_presence, presence, timestamp


class PreparedPresenceRowsContractTest(SimpleTestCase):
    def test_prepared_rows_keep_order_null_fallbacks_and_last_duplicate(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(seconds=3)
        first = {
            "user_profile_id": 15,
            "user_profile__email": "one@example.test",
            "last_active_time": None,
            "last_connected_time": None,
            "user_profile__date_joined": start,
        }
        rows = [
            first,
            {**first, "user_profile_id": 8, "user_profile__email": "two@example.test"},
            {**first, "last_active_time": end},
        ]
        for slim in (True, False):
            with (
                patch.object(native_presence, "_rows_backend", None),
                patch.object(native_presence, "_backend", None),
                patch.object(native_presence, "_modern_backend", None),
                patch.object(native_presence, "_legacy_display_backend", None),
            ):
                expected = presence.get_presence_dicts_for_rows(rows, slim)
            result = presence.get_presence_dicts_for_rows(rows, slim)
            self.assertEqual(list(result.items()), list(expected.items()))
        self.assertEqual(
            presence.get_presence_dicts_for_rows(rows, True)["15"],
            {"active_timestamp": int(end.timestamp()), "idle_timestamp": int(start.timestamp())},
        )

    def test_exact_rows_are_dispatched_and_validator_override_still_runs(self) -> None:
        backend = native_presence._rows_backend
        if getattr(backend, "PREPARED_PRESENCE_ROWS_API", None) != 1:
            self.skipTest("Matching Rust prepared presence rows contract is not available")
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        row = {
            "user_profile_id": 5,
            "user_profile__email": "synthetic@example.test",
            "last_active_time": start,
            "last_connected_time": None,
            "user_profile__date_joined": start,
        }
        with patch.object(
            backend, "format_presence_rows", wraps=backend.format_presence_rows
        ) as dispatch:
            self.assertEqual(list(presence.get_presence_dicts_for_rows([row], True)), ["5"])
            self.assertEqual(
                list(presence.get_presence_dicts_for_rows([row], False)),
                ["synthetic@example.test"],
            )
            self.assertEqual(dispatch.call_count, 2)
            with patch.object(timestamp, "verify_UTC", wraps=timestamp.verify_UTC) as verify:
                presence.get_presence_dicts_for_rows([row], True)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(
            presence.get_presence_dicts_for_rows(
                [{**row, "last_active_time": start.replace(tzinfo=ZoneInfo("UTC"))}], True
            ),
            presence.get_presence_dicts_for_rows([row], True),
        )
