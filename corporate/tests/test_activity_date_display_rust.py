import os
from datetime import date, datetime, timedelta, timezone
from importlib import import_module
from typing import cast, override
from unittest import TestCase
from unittest.mock import patch

from django.utils.translation import override as language_override

from corporate.lib import activity


class ActivityDateDisplayRustTest(TestCase):
    def test_native_fixed_spellings_and_exact_fields(self) -> None:
        try:
            native = import_module("_zulip_backend")
            day = native.format_activity_display_day
            minute = native.format_activity_display_minute
        except (ImportError, AttributeError):
            self.skipTest("Rust activity date display is unavailable")

        self.assertEqual(day(1, 1, 1), "0001-01-01")
        self.assertEqual(day(9999, 12, 31), "9999-12-31")
        self.assertEqual(minute(1, 2, 3, 4, 5), "0001-02-03 04:05")
        self.assertEqual(minute(9999, 12, 31, 23, 59), "9999-12-31 23:59")
        for invalid in (None, True, -1, 0, 10000, 2**90):
            self.assertIsNone(day(invalid, 1, 1))
        self.assertIsNone(day(1, 13, 1))
        self.assertIsNone(minute(1, 1, 1, 24, 0))
        self.assertIsNone(minute(1, 1, 1, 0, 60))

    def test_real_date_cells_use_original_wall_time_and_fixed_notation(self) -> None:
        day = activity._rust_format_activity_display_day
        minute = activity._rust_format_activity_display_minute
        enabled = os.environ.get("ZULIP_RUST_ACTIVITY_DATE_DISPLAY") == "1"
        self.assertEqual(callable(day), enabled)
        self.assertEqual(callable(minute), enabled)
        instant = datetime(
            4, 2, 29, 1, 7, 59, 999999, tzinfo=timezone(timedelta(hours=5, minutes=30)), fold=1
        )
        with (
            language_override("fa"),
            patch.object(activity, "_rust_format_activity_display_day", wraps=day)
            if enabled
            else patch.object(activity, "_rust_format_activity_display_day", None) as date_render,
            patch.object(activity, "_rust_format_activity_display_minute", wraps=minute)
            if enabled
            else patch.object(
                activity, "_rust_format_activity_display_minute", None
            ) as minute_render,
        ):
            self.assertEqual(activity.format_datetime_as_date(instant), "0004-02-29")
            self.assertEqual(activity.format_optional_datetime(instant, True), "0004-02-29 01:07")
            if date_render is not None:
                date_render.assert_called_once_with(4, 2, 29)
            if minute_render is not None:
                minute_render.assert_called_once_with(4, 2, 29, 1, 7)
        self.assertEqual(activity.format_optional_datetime(None), "")
        self.assertEqual(activity.format_optional_datetime(None, True), "None")
        self.assertEqual(
            activity.format_optional_datetime(datetime.max.replace(tzinfo=timezone.utc)),
            "9999-12-31 23:59",
        )

    def test_custom_objects_keep_original_dispatch_and_single_preparation(self) -> None:
        calls: list[object] = []

        class CustomDate(date):
            @override
            def isoformat(self) -> str:
                calls.append("day isoformat")
                return "custom-day"

        class CustomDatetime(datetime):
            @override
            def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
                calls.append((sep, timespec))
                return "custom-time"

        class DateSource:
            def date(self) -> CustomDate:
                calls.append("date")
                return CustomDate(1990, 1, 2)

            def replace(self, *, tzinfo: None) -> CustomDatetime:
                calls.append(("replace", tzinfo))
                return CustomDatetime(1990, 1, 2)

        value = cast("datetime", DateSource())
        with (
            patch.object(activity, "_rust_format_activity_display_day") as day,
            patch.object(activity, "_rust_format_activity_display_minute") as minute,
        ):
            self.assertEqual(activity.format_datetime_as_date(value), "custom-day")
            self.assertEqual(activity.format_optional_datetime(value), "custom-time")
            day.assert_not_called()
            minute.assert_not_called()
        self.assertEqual(calls, ["date", "day isoformat", ("replace", None), (" ", "minutes")])

    def test_decline_and_malformed_wheels_use_python_isoformat(self) -> None:
        instant = datetime(100, 3, 4, 5, 8, 59, tzinfo=timezone(timedelta(hours=-10)))
        for unsupported in (None, (), False):
            with (
                patch.object(
                    activity, "_rust_format_activity_display_day", return_value=unsupported
                ) as day,
                patch.object(
                    activity, "_rust_format_activity_display_minute", return_value=unsupported
                ) as minute,
            ):
                self.assertEqual(activity.format_datetime_as_date(instant), "0100-03-04")
                self.assertEqual(activity.format_optional_datetime(instant), "0100-03-04 05:08")
                day.assert_called_once_with(100, 3, 4)
                minute.assert_called_once_with(100, 3, 4, 5, 8)
