import os
from datetime import datetime, timedelta, timezone
from importlib import import_module
from unittest import TestCase
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from typing_extensions import override

from analytics.lib.counts import COUNT_STATS
from analytics.models import RealmCount
from analytics.views import stats


class ClientSeriesRustTest(TestCase):
    def test_aliases_and_merge(self) -> None:
        try:
            rewrite = import_module("_zulip_backend").rewrite_analytics_client_series
        except (ImportError, AttributeError):
            self.skipTest("Rust chart client series transformation is not available")

        unchanged = int("1234567890")
        label = "value:custom chart".removeprefix("value:")
        values = {
            "ZulipMobile/flutter": [1, 2, unchanged],
            label: [unchanged],
            "website": [2],
            "Web app": [],
            "ZulipFlutter": [9, 7],
            "ZulipExample😀Webhook": [3],
            "desktop app extras": [4],
        }
        labels = stats._client_series_stable_labels()
        output = rewrite(values, labels)
        self.assertEqual(
            list(output.items()),
            [
                ("Mobile app (Flutter)", [10, 9, unchanged]),
                (label, [unchanged]),
                ("Web app", [2]),
                ("Example😀 webhook", [3]),
                ("Old desktop app", [4]),
            ],
        )
        self.assertIs(next(iter(output)), stats.client_label_map("ZulipMobile/flutter"))
        self.assertIs(list(output)[1], label)
        self.assertIs(output["Mobile app (Flutter)"][2], unchanged)
        self.assertIsNot(output[label], values[label])
        self.assertEqual(values["ZulipMobile/flutter"], [1, 2, unchanged])
        empty = rewrite({"ZulipWebhook": []}, labels)
        self.assertEqual(empty, {" webhook": []})
        self.assertIs(next(iter(empty)), stats.client_label_map("ZulipWebhook"))
        self.assertEqual(rewrite({}, labels), {})
        self.assertIsNone(rewrite({"website": [1]}, {"Web app": "Wrong label"}))

        for invalid in (
            {"website": [], "Web app": [1]},
            {"website": [2**63 - 1], "Web app": [1]},
            {"website": [2**80]},
            {"website": [True]},
            {"website": (1,)},
            {"\ud800": [1]},
        ):
            self.assertIsNone(rewrite(invalid, labels))


class ClientSeriesIntegrationTest(SimpleTestCase):
    def test_existing_chart_path_only_rewrites_selected_client_arrays(self) -> None:
        enabled = os.environ.get("ZULIP_RUST_ANALYTICS_CLIENT_SERIES") == "1"
        helper = stats._rewrite_analytics_client_series
        if enabled:
            self.assertIsNotNone(helper)
        else:
            self.assertIsNone(helper)
        native_spy = (
            patch.object(stats, "_rewrite_analytics_client_series", wraps=helper)
            if enabled
            else patch.object(stats, "_rewrite_analytics_client_series", None)
        )
        first = datetime(2020, 1, 1, tzinfo=timezone.utc)
        second = first + timedelta(days=1)
        query = Mock()
        query.filter.return_value.values_list.return_value = [
            ("client-1", first, 2),
            ("client-1", second, 3),
            ("client-2", first, 5),
            ("not-selected", second, 1000),
        ]
        labels: dict[str | None, str] = {
            "client-1": "website",
            "client-2": "Web app",
            "absent": "custom",
        }
        with (
            native_spy as native,
            patch.object(stats, "table_filtered_to_id", return_value=query) as source,
        ):
            stat = COUNT_STATS["messages_sent:client:day"]
            data = stats.get_time_series_by_subgroup(
                stat, RealmCount, 999, [first, second], labels, include_empty_subgroups=True
            )
            self.assertEqual(list(data.items()), [("Web app", [7, 3]), ("custom", [0, 0])])
            self.assertIs(next(iter(data)), stats.client_label_map("website"))
            source.assert_called_once_with(RealmCount, 999)
            query.filter.assert_called_once_with(property=stat.property)
            query.filter.return_value.values_list.assert_called_once_with(
                "subgroup", "end_time", "value"
            )
            if enabled:
                assert native is not None
                native.assert_called_once_with(
                    {"website": [2, 3], "Web app": [5, 0], "custom": [0, 0]},
                    stats._rust_client_series_stable_labels,
                )
                native.reset_mock()

            other = COUNT_STATS["messages_sent:message_type:day"]
            self.assertEqual(
                stats.get_time_series_by_subgroup(
                    other, RealmCount, 999, [first, second], labels, include_empty_subgroups=False
                ),
                {"website": [2, 3], "Web app": [5, 0]},
            )
            if enabled:
                assert native is not None
                native.assert_not_called()

    def test_key_and_value_identity_and_unequal_array_fallback(self) -> None:
        friendly = "value:Web app".removeprefix("value:")
        untouched = int("123456789999")
        raw_first = {friendly: [2, untouched], "website": [5]}
        native_result = stats.rewrite_client_arrays(raw_first)
        self.assertEqual(native_result, {"Web app": [7, untouched]})
        self.assertIs(next(iter(native_result)), friendly)
        self.assertIs(native_result[friendly][1], untouched)
        self.assertIsNot(native_result[friendly], raw_first[friendly])
        self.assertEqual(raw_first[friendly], [2, untouched])

        alias_first = {"website": [2, untouched], friendly: [5]}
        old_first = stats.rewrite_client_arrays(alias_first)
        self.assertIs(next(iter(old_first)), stats.client_label_map("website"))
        self.assertIsNot(next(iter(old_first)), friendly)
        self.assertEqual(old_first, native_result)

        unsupported = {"website": [], friendly: [5]}
        with self.assertRaisesRegex(IndexError, "list index out of range"):
            stats.rewrite_client_arrays(unsupported)
        self.assertEqual(unsupported, {"website": [], friendly: [5]})

    def test_unsupported_numbers_and_dynamic_array_match_python(self) -> None:
        class DynamicArray(list[int]):
            @override
            def copy(self) -> list[int]:
                return [42]

        self.assertEqual(
            stats.rewrite_client_arrays({"custom": DynamicArray([9])}), {"custom": [42]}
        )
        self.assertEqual(
            stats.rewrite_client_arrays({"website": [2**63 - 1], "Web app": [1]}),
            {"Web app": [2**63]},
        )
        self.assertEqual(
            stats.rewrite_client_arrays({"\ud800": [2**81]}),
            {"\ud800": [2**81]},
        )
        self.assertIs(stats.rewrite_client_arrays({"raw": [True]})["raw"][0], True)
