import os
from datetime import datetime, timedelta, timezone
from importlib import import_module
from unittest import TestCase
from unittest.mock import Mock, patch

from analytics.lib.counts import COUNT_STATS
from analytics.models import RealmCount
from analytics.views import stats
from zerver.lib.native_backend import supports_default_rust_backend


class SubgroupProjectionRustTest(TestCase):
    def test_ordered_already_eligible_projection(self) -> None:
        try:
            project = import_module("_zulip_backend").project_analytics_subgroup_series
        except (ImportError, AttributeError):
            self.skipTest("Rust chart subgroup projection is not available")

        big = 2**90
        row_a = "one:ﬁrst label".removeprefix("one:")
        row_b = "two:ﬁrst label".removeprefix("two:")
        self.assertIsNot(row_a, row_b)
        rows = [("😀", 2, 9), (None, 1, 4), ("😀", 2, big), ("", 1, -7)]
        times = [2, 1, 2, 4]
        eligible = [(None, row_a), ("unseen", "zero"), ("😀", row_b), ("", "negative")]
        output = project(rows, times, eligible)
        self.assertEqual(
            list(output.items()),
            [(row_a, [big, 0, big, 0]), ("zero", [0, 0, 0, 0]), ("negative", [0, -7, 0, 0])],
        )
        self.assertIs(next(iter(output)), row_a)
        self.assertIs(output[row_a][0], big)
        self.assertEqual(project(rows, [], eligible), {row_a: [], "zero": [], "negative": []})
        self.assertEqual(project(rows, times, []), {})
        self.assertEqual(project([], [], [(None, "\ud800")]), {"\ud800": []})

        invalid_cases: tuple[tuple[object, object, object], ...] = (
            ([("a", True, 5)], [], []),
            ([("a", 1, True)], [], []),
            ([("a", 1)], [], []),
            (rows, [2**90], eligible),
            (rows, times, [("a",)]),
            (rows, times, [("a", 5)]),
        )
        for inputs in invalid_cases:
            self.assertIsNone(project(*inputs))

    def test_subgroup_keys_preserve_every_surrogate_without_merging_null_and_empty(self) -> None:
        try:
            native = import_module("_zulip_backend")
        except ImportError:
            self.skipTest("Rust chart subgroup projection is not available")
        if getattr(native, "ANALYTICS_SUBGROUP_PROJECTION_API", None) != 1:
            self.skipTest("Wheel predates surrogate-safe subgroup projection")
        count = 2**90
        for point in range(0xD800, 0xE000):
            key = "group-" + chr(point)
            label = "label-" + chr(point)
            rows = [(None, 0, 2), ("", 0, 3), (key, 1, 4), (key, 1, count)]
            eligible = [(None, "null"), ("", "empty"), (key, label)]
            values = native.project_analytics_subgroup_series(rows, [1, 0, 1], eligible)
            self.assertEqual(
                list(values.items()),
                [("null", [0, 2, 0]), ("empty", [0, 3, 0]), (label, [count, 0, count])],
            )
            self.assertIs(list(values)[2], label)
            self.assertIs(values[label][0], count)

    def test_real_chart_query_eligibility_and_order_are_unchanged(self) -> None:
        first = datetime(2020, 1, 1, tzinfo=timezone.utc)
        second = first + timedelta(microseconds=1)
        unused = datetime.max.replace(tzinfo=timezone.utc)
        zero = datetime.min.replace(tzinfo=timezone.utc)
        label_one = "one:نمودار".removeprefix("one:")
        label_two = "two:نمودار".removeprefix("two:")
        large = 2**91
        native = stats._project_analytics_subgroup_series
        enabled = os.environ.get("ZULIP_RUST_ANALYTICS_SUBGROUP_PROJECTION") == "1" or (
            supports_default_rust_backend()
            and getattr(stats._rust_subgroup_projection, "ANALYTICS_SUBGROUP_PROJECTION_API", None)
            == 1
        )
        self.assertEqual(native is not None, enabled)
        spy = Mock(wraps=native) if native is not None else None
        query = Mock()
        query.filter.return_value.values_list.return_value = [
            (None, first, 5),
            ("other", first, 999),
            ("second", second, 8),
            ("second", second.replace(fold=1), large),
            ("outside", unused, -6),
        ]
        labels: dict[str | None, str] = {
            None: label_one,
            "missing": "empty",
            "second": label_two,
            "outside": "outside",
        }
        stat = COUNT_STATS["messages_sent:message_type:day"]
        with (
            patch.object(stats, "table_filtered_to_id", return_value=query) as select,
            patch.object(stats, "_project_analytics_subgroup_series", spy),
        ):
            output = stats.get_time_series_by_subgroup(
                stat, RealmCount, 7, [second, first, second, zero], labels, True
            )
        self.assertEqual(
            list(output.items()),
            [
                (label_one, [large, 0, large, 0]),
                ("empty", [0, 0, 0, 0]),
                ("outside", [0, 0, 0, 0]),
            ],
        )
        self.assertIs(next(iter(output)), label_one)
        self.assertIs(output[label_one][0], large)
        self.assertIsNot(output["empty"], output["outside"])
        select.assert_called_once_with(RealmCount, 7)
        query.filter.assert_called_once_with(property=stat.property)
        query.filter.return_value.values_list.assert_called_once_with(
            "subgroup", "end_time", "value"
        )
        if spy is not None:
            epoch = stats._chart_time_in_microseconds(first)
            spy.assert_called_once_with(
                [
                    (None, epoch, 5),
                    ("second", epoch + 1, large),
                    ("outside", 315537897599999999, -6),
                ],
                [epoch + 1, epoch, epoch + 1, 0],
                list(labels.items()),
            )

    def test_non_utc_equal_keys_and_int_subclasses_keep_python_semantics(self) -> None:
        end = datetime(2020, 1, 1, tzinfo=timezone.utc)
        same = end.astimezone(timezone(timedelta(hours=-3)))
        stat = COUNT_STATS["messages_sent:message_type:day"]
        query = Mock()
        query.filter.return_value.values_list.return_value = [(None, same, 17)]
        with (
            patch.object(stats, "table_filtered_to_id", return_value=query),
            patch.object(stats, "_project_analytics_subgroup_series") as native,
        ):
            self.assertEqual(
                stats.get_time_series_by_subgroup(stat, RealmCount, 7, [end], {None: "one"}, True),
                {"one": [17]},
            )
            query.filter.return_value.values_list.return_value = [(None, end, True)]
            self.assertIs(
                stats.get_time_series_by_subgroup(stat, RealmCount, 7, [end], {None: "one"}, True)[
                    "one"
                ][0],
                True,
            )
        native.assert_not_called()

    def test_native_decline_falls_back_without_replaying_the_queryset(self) -> None:
        end = datetime(2020, 1, 1, tzinfo=timezone.utc)
        stat = COUNT_STATS["messages_sent:message_type:day"]
        query = Mock()
        query.filter.return_value.values_list.return_value = iter([(None, end, 3)])
        with (
            patch.object(stats, "table_filtered_to_id", return_value=query),
            patch.object(stats, "_project_analytics_subgroup_series", return_value=None) as native,
        ):
            self.assertEqual(
                stats.get_time_series_by_subgroup(
                    stat, RealmCount, 7, [end], {None: "one", "none": "not selected"}, False
                ),
                {"one": [3]},
            )
        query.filter.return_value.values_list.assert_called_once_with(
            "subgroup", "end_time", "value"
        )
        token = stats._chart_time_in_microseconds(end)
        native.assert_called_once_with([(None, token, 3)], [token], [(None, "one")])
