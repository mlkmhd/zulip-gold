from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from zerver.lib import timestamp


class UTCCeilingRustTest(TestCase):
    def test_exact_boundary_before_epoch_and_overflow(self) -> None:
        example = datetime(1969, 12, 31, 23, 49, tzinfo=timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(timestamp.ceiling_to_hour(example), epoch)
        self.assertEqual(timestamp.ceiling_to_day(example), epoch)
        with self.assertRaises(OverflowError):
            timestamp.ceiling_to_day(datetime.max.replace(tzinfo=timezone.utc))
        with self.assertRaises(timestamp.TimeZoneNotUTCError):
            timestamp.ceiling_to_hour(epoch.astimezone(timezone(timedelta(hours=2))))

    def test_aligned_time_does_not_build_delta(self) -> None:
        aligned = datetime(2026, 11, 26, tzinfo=timezone.utc)
        with (
            patch.object(timestamp, "floor_to_hour", return_value=aligned) as floor,
            patch.object(timestamp, "timedelta", side_effect=AssertionError("unneeded step")),
        ):
            self.assertIs(timestamp.ceiling_to_hour(aligned), aligned)
        floor.assert_called_once_with(aligned)

    def test_both_actual_call_sites_dispatch_native(self) -> None:
        choose = timestamp._make_prepared_utc_ceiling
        if choose is None:
            self.skipTest("requires the current native UTC ceiling extension")
        aligned = datetime(2026, 11, 26, tzinfo=timezone.utc)
        with patch.object(timestamp, "_make_prepared_utc_ceiling", wraps=choose) as native:
            timestamp.ceiling_to_hour(aligned)
            timestamp.ceiling_to_day(aligned)
        self.assertEqual([record.args[-1] for record in native.call_args_list], [False, True])
