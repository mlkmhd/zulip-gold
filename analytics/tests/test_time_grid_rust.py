from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from analytics.lib import time_utils
from analytics.lib.counts import CountStat
from zerver.lib.timestamp import TimeZoneNotUTCError


class AnalyticsTimeGridRustTest(TestCase):
    def test_actual_time_range_is_ascending_and_can_left_pad(self) -> None:
        start = datetime(2026, 11, 26, 15, 10, tzinfo=timezone.utc)
        end = datetime(2026, 11, 26, 17, 59, tzinfo=timezone.utc)
        self.assertEqual(
            time_utils.time_range(start, end, CountStat.HOUR, 4),
            [datetime(2026, 11, 26, hour, tzinfo=timezone.utc) for hour in (14, 15, 16, 17)],
        )
        self.assertEqual(time_utils.time_range(end, start, CountStat.HOUR, None), [])

    def test_existing_errors_are_not_narrowed(self) -> None:
        start = datetime(1, 1, 1, tzinfo=timezone.utc)
        with self.assertRaises(OverflowError):
            time_utils.time_range(start, start, CountStat.DAY, None)
        with self.assertRaisesRegex(AssertionError, "Unknown frequency: fortnight"):
            time_utils.time_range(start, start, "fortnight", None)
        with self.assertRaises(TimeZoneNotUTCError):
            time_utils.time_range(start.replace(tzinfo=None), start, CountStat.DAY, None)

    def test_current_extension_receives_validated_and_prepared_time_range(self) -> None:
        walk = time_utils._walk_prepared_analytics_time_grid
        if walk is None:
            self.skipTest("requires the current native time-grid walker")
        floor = datetime(2026, 11, 26, 15, tzinfo=timezone.utc)
        with patch.object(time_utils, "_walk_prepared_analytics_time_grid", wraps=walk) as native:
            self.assertEqual(
                time_utils.time_range(floor, floor + timedelta(minutes=59), CountStat.HOUR, None),
                [floor],
            )
        native.assert_called_once_with(floor, floor, timedelta(hours=1))
