from datetime import datetime, timedelta, timezone
from time import struct_time
from unittest import TestCase
from unittest.mock import Mock, patch

from typing_extensions import override

from zerver.lib import timestamp


class UTCFloorRustTest(TestCase):
    def test_boundaries_and_same_tz_validation(self) -> None:
        time = datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
        self.assertEqual(
            timestamp.floor_to_hour(time), datetime(1969, 12, 31, 23, tzinfo=timezone.utc)
        )
        self.assertEqual(timestamp.floor_to_day(time), datetime(1969, 12, 31, tzinfo=timezone.utc))
        with self.assertRaises(timestamp.TimeZoneNotUTCError):
            timestamp.floor_to_day(time.astimezone(timezone(timedelta(hours=1))))

    def test_datetime_subclass_stays_on_the_original_path(self) -> None:
        class CustomDate(datetime):
            @override
            def timetuple(self) -> struct_time:
                return datetime(2026, 11, 25, 9, tzinfo=timezone.utc).timetuple()

        time = CustomDate(2026, 11, 26, 8, tzinfo=timezone.utc)
        native = Mock(side_effect=AssertionError("subclass uses the existing API"))
        with patch.object(timestamp, "_make_prepared_utc_floor", native):
            self.assertEqual(
                timestamp.floor_to_hour(time), datetime(2026, 11, 25, 9, tzinfo=timezone.utc)
            )
        native.assert_not_called()

    def test_both_functions_use_current_native_for_exact_datetimes(self) -> None:
        available = timestamp._make_prepared_utc_floor
        if available is None:
            self.skipTest("requires the current UTC rounding contract")
        time = datetime(2026, 11, 26, 1, 2, tzinfo=timezone.utc)
        with patch.object(timestamp, "_make_prepared_utc_floor", wraps=available) as native:
            timestamp.floor_to_hour(time)
            timestamp.floor_to_day(time)
        self.assertEqual([c.args[-1] for c in native.call_args_list], [False, True])
