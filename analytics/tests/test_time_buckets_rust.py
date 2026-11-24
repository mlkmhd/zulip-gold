import os
from datetime import datetime, timedelta, timezone
from importlib import import_module
from unittest import TestCase
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from analytics.lib.counts import CountStat
from analytics.lib.time_utils import time_range
from analytics.views import stats
from zerver.lib.timestamp import TimeZoneNotUTCError
from zerver.models import Realm, UserProfile


class AnalyticsTimeBucketsRustTest(TestCase):
    def test_numeric_range_and_fallback(self) -> None:
        try:
            plan = import_module("_zulip_backend").plan_analytics_time_buckets
        except (ImportError, AttributeError):
            self.skipTest("Rust analytics chart bucket planner is not available")

        hour = 3_600_000_000
        day = hour * 24
        full = 3_652_059 * day - 1
        self.assertEqual(plan(3 * day + 1, 5 * day, day, None), (4 * day, 2, day))
        self.assertEqual(plan(3 * day + 1, 5 * day, day, 4), (2 * day, 4, day))
        self.assertEqual(plan(3 * day + 1, 3 * day + 20, hour, None), (0, 0, hour))
        self.assertEqual(plan(3 * day, 3 * day, day, 0), (3 * day, 1, day))
        self.assertEqual(plan(5 * day, 4 * day, day, 1), (4 * day, 1, day))
        self.assertEqual(plan(5 * day, 3 * day, day, -1), (0, 0, day))
        self.assertEqual(plan(full, full, day, None), (0, 0, day))

        for args in (
            (0, day, day, None),
            (0, 0, day, None),
            (day, day, day, 3),
            (full, full, day, 0),
            (day, day, day, 2**100),
            (day, day, day, -(2**63)),
            (day, day, day, True),
            (day, day, 60, None),
            (-1, day, day, None),
            (day, full + 1, day, None),
            (day, 65_537 * day, day, None),
        ):
            self.assertIsNone(plan(*args))


class AnalyticsTimeBucketsIntegrationTest(SimpleTestCase):
    def test_actual_chart_read_uses_prepared_buckets_only(self) -> None:
        enabled = os.environ.get("ZULIP_RUST_ANALYTICS_TIME_BUCKETS") == "1"
        helper = stats._plan_analytics_time_buckets
        if enabled:
            self.assertIsNotNone(helper)
        else:
            self.assertIsNone(helper)
        native_spy = (
            patch.object(stats, "_plan_analytics_time_buckets", wraps=helper)
            if enabled
            else patch.object(stats, "_plan_analytics_time_buckets", None)
        )
        start = datetime(2020, 1, 2, 5, 19, 0, 23, tzinfo=timezone.utc)
        end = datetime(2020, 1, 2, 6, 17, 3, 42, tzinfo=timezone.utc)
        expected = time_range(start, end, CountStat.HOUR, 3)
        request = RequestFactory().get("/synthetic-chart")
        user = UserProfile(id=313, role=UserProfile.ROLE_MEMBER)
        realm = Realm(id=211)
        with (
            native_spy as native,
            patch.object(stats, "get_time_series_by_subgroup", return_value={}) as series,
            patch.object(stats, "json_success", return_value=HttpResponse()) as response,
        ):
            self.assertEqual(
                stats.do_get_chart_data(
                    request,
                    user,
                    chart_name="messages_sent_over_time",
                    min_length=3,
                    start=start,
                    end=end,
                    realm=realm,
                ).status_code,
                200,
            )
            response.assert_called_once()
            self.assertIs(response.call_args.args[0], request)
            data = response.call_args.kwargs["data"]
            self.assertEqual(data["end_times"], [int(value.timestamp()) for value in expected])
            self.assertEqual(data["frequency"], CountStat.HOUR)
            self.assertEqual(series.call_count, 2)
            for args in series.call_args_list:
                self.assertEqual(args.args[3], expected)
                self.assertTrue(all(value.tzinfo is timezone.utc for value in args.args[3]))
            if enabled:
                assert native is not None
                native.assert_called_once_with(
                    stats._chart_time_in_microseconds(start),
                    stats._chart_time_in_microseconds(end),
                    3_600_000_000,
                    3,
                )

    def test_builtin_realization_and_exotic_input_fallback(self) -> None:
        start = datetime(2024, 2, 29, 1, 30, tzinfo=timezone.utc)
        end = datetime(2024, 3, 2, 0, 0, 1, tzinfo=timezone.utc, fold=1)
        for frequency, minimum in ((CountStat.DAY, None), (CountStat.HOUR, 0), ("day", -1)):
            expected = time_range(start, end, frequency, minimum)
            actual = stats._chart_time_range(start, end, frequency, minimum)
            self.assertEqual(actual, expected)
            self.assertTrue(all(type(value) is datetime for value in actual))
            self.assertTrue(
                all(value.tzinfo is timezone.utc and value.fold == 0 for value in actual)
            )

        class CustomDate(datetime):
            pass

        class CustomFrequency(str):
            __slots__ = ()

        offset_zero = timezone(timedelta(0), "Synthetic offset zero")
        with patch.object(
            stats, "_plan_analytics_time_buckets", side_effect=AssertionError("unsupported input")
        ):
            cases = (
                (start.replace(tzinfo=offset_zero), end, "hour", 2),
                (CustomDate(2024, 3, 1, tzinfo=timezone.utc), end, "day", 1),
                (start, end, CustomFrequency("day"), None),
                (start, end, "day", True),
            )
            for args in cases:
                self.assertEqual(stats._chart_time_range(*args), time_range(*args))

    def test_legacy_date_and_frequency_errors(self) -> None:
        earliest = datetime.min.replace(tzinfo=timezone.utc)
        latest = datetime.max.replace(tzinfo=timezone.utc)
        near_start = earliest + timedelta(days=2)
        for args in (
            (earliest, earliest, "hour", None),
            (latest, latest, "day", 0),
            (near_start, near_start, "day", 2**99),
        ):
            with self.assertRaises(OverflowError) as previous:
                time_range(*args)
            with self.assertRaises(OverflowError) as current:
                stats._chart_time_range(*args)
            self.assertEqual(current.exception.args, previous.exception.args)
        with self.assertRaisesRegex(AssertionError, "Unknown frequency: fortnight"):
            stats._chart_time_range(near_start, near_start, "fortnight", None)
        with self.assertRaises(TimeZoneNotUTCError):
            stats._chart_time_range(near_start.replace(tzinfo=None), near_start, "hour", None)
