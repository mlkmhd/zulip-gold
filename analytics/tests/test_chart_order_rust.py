import os
from importlib import import_module
from typing import cast
from unittest import TestCase
from unittest.mock import patch

from django.test import SimpleTestCase
from typing_extensions import override

from analytics.views import stats
from zerver.lib.native_backend import supports_default_rust_backend


class AnalyticsChartOrderRustTest(TestCase):
    def test_totals_and_client_order(self) -> None:
        try:
            module = import_module("_zulip_backend")
            totals = module.order_analytics_chart_totals
            clients = module.order_analytics_chart_clients
        except (ImportError, AttributeError):
            self.skipTest("Rust chart ordering is not available")

        self.assertEqual(
            totals({"é": [10, -9], "a": [1], "😀": [1], "z": [], "x": [-1]}),
            ["😀", "é", "a", "z", "x"],
        )
        self.assertEqual(totals({}), [])
        realm_a = "realm:same label".removeprefix("realm:")
        user_a = "user:same label".removeprefix("user:")
        self.assertIsNot(realm_a, user_a)
        everyone = {realm_a: [16], "c": [15], "b": [14], "e": [13], "d": [12], "h": [11]}
        user = {user_a: [6], "b": [5], "d": [4], "e": [3], "f": [2], "g": [1]}
        got = clients(everyone, user)
        self.assertEqual(got, [realm_a, "b", "c", "d", "e", "f", "g", "h"])
        self.assertIs(got[0], realm_a)
        self.assertEqual(clients({}, user), [user_a, "b", "d", "e", "f", "g"])
        self.assertIs(clients({}, user)[0], user_a)
        for invalid in (
            {"a": [True]},
            {"a": [2**63 - 1, 1, -1]},
            {"a": [2**90]},
            {"a": (1,)},
        ):
            self.assertIsNone(totals(invalid))
            self.assertIsNone(clients({}, invalid))

    def test_order_keeps_every_surrogate_and_the_original_key(self) -> None:
        try:
            module = import_module("_zulip_backend")
        except ImportError:
            self.skipTest("Rust chart ordering is not available")
        if getattr(module, "ANALYTICS_CHART_ORDER_API", None) != 1:
            self.skipTest("Wheel predates surrogate-safe chart ordering")
        for point in range(0xD800, 0xE000):
            realm = ("realm:" + chr(point)).removeprefix("realm:")
            user = ("user:" + chr(point)).removeprefix("user:")
            # One-character string storage may be shared, so use decorated
            # values when checking that the first equal key is preserved.
            realm = "chart-" + realm
            user = "chart-" + user
            everyone = {realm: [1], "chart-\ud7ff": [1], "chart-\ue000": [1], "chart-😀": [1]}
            own = {user: [9], "personal": [2]}
            expected = sorted(everyone, reverse=True)
            self.assertEqual(module.order_analytics_chart_totals(everyone), expected)
            ranked = module.order_analytics_chart_clients(everyone, own)
            self.assertEqual(ranked, [realm, expected[0], "personal", expected[1], expected[3]])
            self.assertIs(ranked[0], realm)


class AnalyticsChartOrderIntegrationTest(SimpleTestCase):
    def test_actual_chart_order_wrappers_and_startup_contract(self) -> None:
        enabled = os.environ.get("ZULIP_RUST_ANALYTICS_CHART_ORDER") == "1" or (
            supports_default_rust_backend()
            and getattr(stats._rust_chart_order, "ANALYTICS_CHART_ORDER_API", None) == 1
        )
        totals = stats._order_analytics_chart_totals
        clients = stats._order_analytics_chart_clients
        if enabled:
            self.assertIsNotNone(totals)
            self.assertIsNotNone(clients)
        else:
            self.assertIsNone(totals)
            self.assertIsNone(clients)
        totals_spy = (
            patch.object(stats, "_order_analytics_chart_totals", wraps=totals)
            if enabled
            else patch.object(stats, "_order_analytics_chart_totals", None)
        )
        clients_spy = (
            patch.object(stats, "_order_analytics_chart_clients", wraps=clients)
            if enabled
            else patch.object(stats, "_order_analytics_chart_clients", None)
        )
        realm_label = "one:equal label".removeprefix("one:")
        user_label = "two:equal label".removeprefix("two:")
        self.assertIsNot(realm_label, user_label)
        everyone = {realm_label: [3], "😀": [1], "é": [1], "a": [1]}
        user = {user_label: [17], "additional": [6]}
        data = cast(
            "dict[str, dict[str, list[int]]]",
            {"everyone": everyone, "user": user, "frequency": "day", "end_times": [1]},
        )
        with totals_spy as native_totals, clients_spy as native_clients:
            self.assertEqual(stats.sort_by_totals(everyone), [realm_label, "😀", "é", "a"])
            ranked = stats.sort_client_labels(data)
            self.assertEqual(ranked, [realm_label, "additional", "😀", "é", "a"])
            self.assertIs(ranked[0], realm_label)
            if enabled:
                assert native_totals is not None
                assert native_clients is not None
                native_totals.assert_called_once_with(everyone)
                native_clients.assert_called_once_with(everyone, user)
                self.assertIs(native_clients.call_args.args[0], everyone)
                self.assertIs(native_clients.call_args.args[1], user)

    def test_outer_guards_keep_exact_python_lookup_and_error_order(self) -> None:
        lookups: list[str] = []

        class TraceData(dict[str, dict[str, list[int]]]):
            @override
            def __getitem__(self, key: str) -> dict[str, list[int]]:
                lookups.append(key)
                return super().__getitem__(key)

        class DerivedKey(str):
            __slots__ = ()

        with patch.object(
            stats, "_order_analytics_chart_clients", side_effect=AssertionError("unguarded input")
        ):
            adaptive = TraceData(everyone={"realm": [3]}, user={"user": [6]})
            self.assertEqual(stats.sort_client_labels(adaptive), ["user", "realm"])
            self.assertEqual(lookups, ["everyone", "user"])

            plain_with_derived_key = {DerivedKey("everyone"): {"x": [1]}, "user": {}}
            self.assertEqual(stats.sort_client_labels(plain_with_derived_key), ["x"])

            with self.assertRaisesRegex(TypeError, "unsupported operand type"):
                stats.sort_client_labels({"everyone": {"a": cast("list[int]", [None])}})
            with self.assertRaises(KeyError) as error:
                stats.sort_client_labels({"everyone": {"a": [1]}})
            self.assertEqual(error.exception.args, ("user",))

    def test_legacy_numeric_order_falls_back_without_mutating_inputs(self) -> None:
        overflow: dict[str, list[int]] = {
            "a": [2**63 - 1, 1, -1],
            "z": [2**90],
            "w": [True],
            "\ud800": [0],
        }
        before = [(key, values.copy()) for key, values in overflow.items()]
        self.assertEqual(stats.sort_by_totals(overflow), ["z", "a", "w", "\ud800"])
        self.assertEqual(
            stats.sort_client_labels({"everyone": overflow, "user": {}}), ["z", "a", "w", "\ud800"]
        )
        self.assertEqual(list(overflow.items()), before)

    def test_rewritten_client_series_composes_with_independent_ordering(self) -> None:
        everyone = stats.rewrite_client_arrays(
            {"website": [9], "ZulipFlutter": [3], "ZulipMobile/flutter": [2], "Other": [1]}
        )
        user = stats.rewrite_client_arrays({"ZulipFlutter": [6], "website": [2], "user": [1]})
        self.assertEqual(everyone, {"Web app": [9], "Mobile app (Flutter)": [5], "Other": [1]})
        expected = ["Mobile app (Flutter)", "Web app", "user", "Other"]
        result = stats.sort_client_labels({"everyone": everyone, "user": user})
        self.assertEqual(result, expected)
        self.assertIs(result[0], next(label for label in everyone if label == expected[0]))
