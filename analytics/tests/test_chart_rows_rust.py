from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import Mock, patch

from analytics.lib.counts import COUNT_STATS
from analytics.models import RealmCount
from analytics.views import stats


class ChartRowsRustTest(TestCase):
    def test_one_query_and_python_ordered_duplicates(self) -> None:
        first = datetime(2026, 11, 26, tzinfo=timezone.utc)
        second = first + timedelta(hours=1)
        equal = second.astimezone(timezone(timedelta(hours=-5)))
        query = Mock()
        values = query.filter.return_value.values_list
        values.return_value = iter(
            [(None, first, 1), ("two", second, 2), (None, first, 9), ("two", equal, 8)]
        )
        stat = COUNT_STATS["messages_sent:message_type:day"]
        with patch.object(stats, "table_filtered_to_id", return_value=query) as table:
            output = stats.get_time_series_by_subgroup(
                stat, RealmCount, 8, [first, second], {"two": "other", None: "none"}, True
            )
        self.assertEqual(list(output.items()), [("other", [0, 8]), ("none", [9, 0])])
        table.assert_called_once_with(RealmCount, 8)
        query.filter.assert_called_once_with(property=stat.property)
        values.assert_called_once_with("subgroup", "end_time", "value")

    def test_non_tuple_row_and_original_unpack_error(self) -> None:
        when = datetime(2026, 11, 26, tzinfo=timezone.utc)
        query = Mock()
        values = query.filter.return_value.values_list
        stat = COUNT_STATS["messages_sent:message_type:day"]
        with patch.object(stats, "table_filtered_to_id", return_value=query):
            values.return_value = iter([[None, when, 2]])
            self.assertEqual(
                stats.get_time_series_by_subgroup(stat, RealmCount, 8, [when], {None: "All"}, True),
                {"All": [2]},
            )
            values.return_value = iter([(None, when)])
            with self.assertRaisesRegex(ValueError, "not enough values to unpack"):
                stats.get_time_series_by_subgroup(stat, RealmCount, 8, [when], {None: "All"}, True)

    def test_actual_chart_function_calls_native_on_selected_rows(self) -> None:
        native = stats._group_prepared_analytics_rows
        if native is None:
            self.skipTest("requires the current native chart row grouping")
        query = Mock()
        query.filter.return_value.values_list.return_value = iter([])
        stat = COUNT_STATS["messages_sent:message_type:day"]
        with (
            patch.object(stats, "table_filtered_to_id", return_value=query),
            patch.object(stats, "_group_prepared_analytics_rows", wraps=native) as group,
        ):
            self.assertEqual(
                stats.get_time_series_by_subgroup(stat, RealmCount, 8, [], {}, True), {}
            )
        group.assert_called_once()
