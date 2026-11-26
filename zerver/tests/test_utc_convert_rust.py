from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from zerver.lib import timestamp


class UTCConvertRustTest(TestCase):
    def test_fixed_offset_exact_second_and_dst_fold(self) -> None:
        offset = timezone(timedelta(seconds=-1))
        value = datetime(2026, 11, 26, 23, 59, 59, tzinfo=offset)
        self.assertEqual(
            timestamp.convert_to_UTC(value), datetime(2026, 11, 27, tzinfo=timezone.utc)
        )
        region = ZoneInfo("America/New_York")
        wall = datetime(2025, 11, 2, 1, 30, tzinfo=region)
        later = timestamp.convert_to_UTC(wall.replace(fold=1))
        earlier = timestamp.convert_to_UTC(wall)
        self.assertEqual(later - earlier, timedelta(hours=1))
        self.assertIs(later.tzinfo, timezone.utc)

    def test_naive_gets_utc_without_using_host_local_timezone(self) -> None:
        value = datetime(2026, 11, 26, 1, 2, 3, 4)  # noqa: DTZ001
        self.assertEqual(timestamp.convert_to_UTC(value), value.replace(tzinfo=timezone.utc))
        self.assertIsNone(value.tzinfo)

    def test_actual_utc_conversion_uses_updated_extension(self) -> None:
        native = timestamp._convert_prepared_utc
        if native is None:
            self.skipTest("requires the current native conversion")
        value = datetime(2026, 11, 26, 1, 2, 3, tzinfo=timezone.utc)
        with patch.object(timestamp, "_convert_prepared_utc", wraps=native) as convert:
            self.assertIs(timestamp.convert_to_UTC(value), value)
        convert.assert_called_once_with(value, timezone.utc)
