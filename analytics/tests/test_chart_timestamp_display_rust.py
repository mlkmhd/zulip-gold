import math
import os
from datetime import datetime, timedelta, timezone
from importlib import import_module
from unittest import TestCase
from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from typing_extensions import override

from analytics.views import stats
from zerver.models import Realm, UserProfile


class ChartTimestampDisplayRustTest(TestCase):
    def test_native_prepared_float_truncation_and_limits(self) -> None:
        try:
            convert = import_module("_zulip_backend").truncate_chart_timestamp_display
        except (ImportError, AttributeError):
            self.skipTest("Rust chart timestamp display is not available")
        edge = float(2**63)
        values = [-edge, math.nextafter(edge, 0), -1.9, -0.9, -0.0, 0.0, 0.9, 1.9]
        self.assertEqual(convert(values), [int(value) for value in values])
        self.assertEqual(convert([]), [])
        invalid: tuple[object, ...] = (
            (1.0,),
            [1],
            [False],
            [edge],
            [math.nextafter(-edge, -math.inf)],
            [float("nan")],
            [float("inf")],
        )
        for value in invalid:
            self.assertIsNone(convert(value))


class ChartTimestampDisplayIntegrationTest(SimpleTestCase):
    def test_actual_chart_keeps_python_timestamps_and_original_bucket_objects(self) -> None:
        native = stats._rust_truncate_chart_timestamp_display
        if os.environ.get("ZULIP_RUST_CHART_TIMESTAMP_DISPLAY") != "1":
            self.assertIsNone(native)
        spy = Mock(wraps=native) if native is not None else None
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        values = [
            epoch - timedelta(seconds=2, microseconds=500000),
            epoch - timedelta(microseconds=999999),
            epoch,
            epoch + timedelta(seconds=2, microseconds=499999),
            datetime.max.replace(tzinfo=timezone.utc),
        ]
        request = RequestFactory().get("/synthetic-chart")
        user = UserProfile(id=21, role=UserProfile.ROLE_MEMBER)
        realm = Realm(id=27)
        with (
            patch.object(stats, "_chart_time_range", return_value=values),
            patch.object(stats, "get_time_series_by_subgroup", return_value={}) as series,
            patch.object(stats, "json_success", return_value=HttpResponse()) as response,
            patch.object(stats, "_rust_truncate_chart_timestamp_display", spy),
        ):
            actual = stats.do_get_chart_data(
                request,
                user,
                chart_name="messages_sent_over_time",
                start=values[0],
                end=values[-1],
                realm=realm,
            )
        self.assertEqual(actual.status_code, 200)
        response.assert_called_once()
        self.assertIs(response.call_args.args[0], request)
        self.assertEqual(
            response.call_args.kwargs["data"]["end_times"],
            [int(value.timestamp()) for value in values],
        )
        for call in series.call_args_list:
            self.assertIs(call.args[3], values)
        self.assertEqual(series.call_count, 2)
        if spy is not None:
            spy.assert_called_once_with([value.timestamp() for value in values])

    def test_noncanonical_time_objects_keep_original_conversion_and_error_order(self) -> None:
        observed: list[int] = []

        class CustomDate(datetime):
            @override
            def timestamp(self) -> float:
                observed.append(self.day)
                return float("nan") if self.day == 1 else 83.0

        with patch.object(stats, "_rust_truncate_chart_timestamp_display") as native:
            with self.assertRaisesRegex(ValueError, "cannot convert float NaN to integer"):
                stats._chart_display_epoch_seconds(
                    [CustomDate(2000, 1, day, tzinfo=timezone.utc) for day in (1, 2)]
                )
            self.assertEqual(observed, [1])
            named_utc = timezone(timedelta(0), "Synthetic UTC offset")
            dt = datetime(1969, 12, 31, 23, 59, 59, 500000, tzinfo=named_utc)
            self.assertEqual(stats._chart_display_epoch_seconds([dt]), [0])
        native.assert_not_called()

    def test_native_decline_keeps_original_signed_truncation(self) -> None:
        date = datetime(1969, 12, 31, 23, 59, 58, 500000, tzinfo=timezone.utc)
        with patch.object(
            stats, "_rust_truncate_chart_timestamp_display", return_value=None
        ) as native:
            self.assertEqual(stats._chart_display_epoch_seconds([date]), [-1])
        native.assert_called_once_with([-1.5])
