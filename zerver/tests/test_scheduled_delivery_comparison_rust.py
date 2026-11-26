from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from zerver.lib import scheduled_messages
from zerver.lib.exceptions import DeliveryTimeNotInFutureError
from zerver.lib.timestamp import timestamp_to_datetime


class ScheduledDeliveryComparisonRustTest(SimpleTestCase):
    def test_utc_comparison_at_microsecond_and_datetime_boundaries(self) -> None:
        native = scheduled_messages._rust_delivery_time_is_future
        witness = Mock(wraps=native) if native is not None else None
        with (
            patch.object(scheduled_messages, "_rust_scheduled_job_timestamp", None),
            patch.object(scheduled_messages, "_rust_delivery_time_is_future", witness),
        ):
            cases = [
                (-62_135_596_800, 0),
                (-1, -1),
                (-1, 0),
                (0, 1),
                (1_725_000_000, -1),
                (1_725_000_000, 0),
                (1_725_000_000, 1),
                (253_402_300_799, -1),
                (253_402_300_799, 1),
            ]
            for timestamp, offset in cases:
                deliver_at = timestamp_to_datetime(timestamp)
                now = deliver_at + timedelta(microseconds=offset)
                with (
                    self.subTest(timestamp=timestamp, offset=offset),
                    patch.object(scheduled_messages, "timezone_now", return_value=now) as clock,
                ):
                    if now < deliver_at:
                        self.assertEqual(
                            scheduled_messages.validate_scheduled_delivery_time(timestamp),
                            deliver_at,
                        )
                    else:
                        with self.assertRaises(DeliveryTimeNotInFutureError):
                            scheduled_messages.validate_scheduled_delivery_time(timestamp)
                    clock.assert_called_once_with()
        if witness is not None:
            self.assertEqual(witness.call_count, len(cases))

    def test_invalid_conversion_precedes_clock_and_native_code(self) -> None:
        with (
            patch.object(scheduled_messages, "_rust_scheduled_job_timestamp", None),
            patch.object(scheduled_messages, "_rust_delivery_time_is_future") as native,
            patch.object(scheduled_messages, "timezone_now") as clock,
        ):
            for timestamp in (-62_135_596_801, 253_402_300_800, 10**400):
                with (
                    self.subTest(timestamp=timestamp),
                    self.assertRaises((OverflowError, ValueError)),
                ):
                    scheduled_messages.validate_scheduled_delivery_time(timestamp)
            clock.assert_not_called()
            native.assert_not_called()

    def test_alternate_converter_and_timezone_keep_python_semantics(self) -> None:
        deliver_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
        now = deliver_at - timedelta(microseconds=1)
        with (
            patch.object(scheduled_messages, "_rust_scheduled_job_timestamp", None),
            patch.object(scheduled_messages, "_rust_delivery_time_is_future") as native,
            patch.object(
                scheduled_messages, "timestamp_to_datetime", return_value=deliver_at
            ) as converter,
            patch.object(scheduled_messages, "timezone_now", return_value=now),
        ):
            self.assertIs(scheduled_messages.validate_scheduled_delivery_time(0), deliver_at)
            converter.assert_called_once_with(0)
            native.assert_not_called()
        adjusted = timezone(timedelta(hours=3, minutes=30))
        with (
            patch.object(scheduled_messages, "_rust_scheduled_job_timestamp", None),
            patch.object(scheduled_messages, "_rust_delivery_time_is_future") as native,
            patch.object(
                scheduled_messages,
                "timezone_now",
                return_value=timestamp_to_datetime(0).astimezone(adjusted),
            ),
            self.assertRaises(DeliveryTimeNotInFutureError),
        ):
            scheduled_messages.validate_scheduled_delivery_time(0)
        native.assert_not_called()
