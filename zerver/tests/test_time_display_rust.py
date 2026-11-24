import os
from datetime import datetime, timedelta, timezone
from importlib import import_module
from unittest import TestCase
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from typing_extensions import override

from zerver.lib import timestamp


class TimeDisplayRustTest(TestCase):
    def test_prepared_fixed_display(self) -> None:
        try:
            module = import_module("_zulip_backend")
            wrap = module.wrap_global_time_display
            gmt = module.format_gmt_display_name
        except (ImportError, AttributeError):
            self.skipTest("Rust time display helpers are not available")

        self.assertEqual(
            wrap("2001-02-03T04:05:06+07:08:09.123456"),
            "<time:2001-02-03T04:05:06+07:08:09.123456>",
        )
        self.assertEqual(wrap("مثال 🌐"), "<time:مثال 🌐>")
        self.assertEqual(gmt("+", 0, 0), "GMT+00:00")
        self.assertEqual(gmt("-", 7, 8), "GMT-07:08")
        self.assertEqual(gmt("-", 0, 0), "GMT-00:00")
        self.assertEqual(gmt("+", 23, 59), "GMT+23:59")
        self.assertIs(type(gmt("+", 23, 59)), str)

        class Derived(str):
            __slots__ = ()

        for invalid in (Derived("x"), "\ud800", 4, b"abc", None):
            self.assertIsNone(wrap(invalid))
        for args in (
            ("", 0, 0),
            (Derived("+"), 0, 0),
            ("+", 7, 60),
            ("-", -1, 59),
            ("-", 24, 0),
            ("+", False, 0),
            ("+", 2**90, 0),
        ):
            self.assertIsNone(gmt(*args))

    def test_global_time_uses_original_iso_preparation_and_format_semantics(self) -> None:
        native = timestamp._rust_wrap_global_time_display
        if os.environ.get("ZULIP_RUST_TIME_DISPLAY") != "1":
            self.assertIsNone(native)
        spy = Mock(wraps=native) if native is not None else None

        class Rendered(str):
            __slots__ = ()

            @override
            def __format__(self, format_spec: str) -> str:
                if format_spec != "":
                    raise ValueError("not the original interpolation")
                return "\ud800مثال 🌐"

        class ExampleDatetime(datetime):
            @override
            def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
                if (sep, timespec) != ("T", "seconds"):
                    raise ValueError("not the original ISO call")
                return Rendered("must retain the original __format__ behavior")

        dt = datetime(2001, 2, 3, 4, 5, 6, 987654, tzinfo=timezone(timedelta(seconds=1)))
        derived = ExampleDatetime(2001, 2, 3, tzinfo=timezone.utc)
        with patch.object(timestamp, "_rust_wrap_global_time_display", spy):
            self.assertEqual(
                timestamp.datetime_to_global_time(dt), "<time:2001-02-03T04:05:06+00:00:01>"
            )
            if spy is not None:
                spy.assert_called_once_with("2001-02-03T04:05:06+00:00:01")
                spy.reset_mock()
            self.assertEqual(timestamp.datetime_to_global_time(derived), "<time:\ud800مثال 🌐>")
            if spy is not None:
                spy.assert_called_once()
                self.assertIs(type(spy.call_args.args[0]), Rendered)
                spy.reset_mock()
            with self.assertRaises(timestamp.TimeZoneMissingError):
                timestamp.datetime_to_global_time(datetime(2001, 2, 3))  # noqa: DTZ001
            if spy is not None:
                spy.assert_not_called()

    def test_gmt_formatting_is_after_validation_and_for_fixed_offsets_only(self) -> None:
        native = timestamp._rust_format_gmt_display_name
        if os.environ.get("ZULIP_RUST_TIME_DISPLAY") != "1":
            self.assertIsNone(native)
        spy = Mock(wraps=native) if native is not None else None
        dt = datetime(2001, 2, 3, 4, 5, 6, tzinfo=timezone(-timedelta(hours=7, minutes=8)))
        named = dt.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        fractional = dt.replace(tzinfo=timezone(timedelta(seconds=1)))
        with (
            patch.object(timestamp, "_rust_format_gmt_display_name", spy),
            patch.object(
                timestamp, "get_icu_time_zone", wraps=timestamp.get_icu_time_zone
            ) as get_zone,
        ):
            self.assertEqual(
                timestamp.format_datetime_to_string(dt, True), "Sat, Feb 3, 2001, 04:05 GMT-7:08"
            )
            get_zone.assert_called_once_with("GMT-07:08")
            if spy is not None:
                spy.assert_called_once_with("-", 7, 8)
                spy.reset_mock()
            get_zone.reset_mock()
            self.assertEqual(
                timestamp.format_datetime_to_string(named, True), "Sat, Feb 3, 2001, 04:05 PST"
            )
            get_zone.assert_called_once_with("America/Los_Angeles")
            get_zone.reset_mock()
            with self.assertRaises(AssertionError):
                timestamp.format_datetime_to_string(fractional, True)
            get_zone.assert_not_called()
            if spy is not None:
                spy.assert_not_called()

    def test_each_decline_uses_the_original_f_strings(self) -> None:
        dt = datetime(2001, 2, 3, 4, 5, 6, tzinfo=timezone(timedelta(hours=7, minutes=8)))
        with (
            patch.object(timestamp, "_rust_wrap_global_time_display", return_value=None) as wrap,
            patch.object(timestamp, "_rust_format_gmt_display_name", return_value=None) as gmt,
            patch.object(
                timestamp, "get_icu_time_zone", wraps=timestamp.get_icu_time_zone
            ) as get_zone,
        ):
            self.assertEqual(
                timestamp.datetime_to_global_time(dt), "<time:2001-02-03T04:05:06+07:08>"
            )
            self.assertEqual(
                timestamp.format_datetime_to_string(dt, True), "Sat, Feb 3, 2001, 04:05 GMT+7:08"
            )
        wrap.assert_called_once_with("2001-02-03T04:05:06+07:08")
        gmt.assert_called_once_with("+", 7, 8)
        get_zone.assert_called_once_with("GMT+07:08")
