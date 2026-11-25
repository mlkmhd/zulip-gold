import os
from importlib import import_module
from typing import SupportsIndex
from unittest import TestCase
from unittest.mock import patch

from typing_extensions import override

from zerver.lib.native_backend import supports_default_rust_backend
from zerver.lib.validator import WildValue, wrap_wild_value
from zerver.webhooks.sonarqube import view


def synthetic_conditions() -> WildValue:
    return wrap_wild_value(
        "conditions",
        [
            {"status": "OK"},
            {
                "metric": "_demo__متن_",
                "status": "OTHER",
                "operator": "GREATER_THAN",
                "value": "2",
                "errorThreshold": "4",
            },
            {"status": "NO_VALUE"},
            {
                "metric": "another_😀",
                "status": "ELEVATED",
                "operator": "UNCHANGED",
                "errorThreshold": "5",
            },
        ],
    )


class Derived(str):
    __slots__ = ()

    @override
    def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
        return ["override", sep or "none"]


class SonarqubeDisplayRustTest(TestCase):
    def test_native_exact_types_whitespace_and_identity(self) -> None:
        try:
            module = import_module("_zulip_backend")
            metric = module.format_sonarqube_metric_label
            join = module.join_sonarqube_condition_lines
        except (ImportError, AttributeError):
            self.skipTest("Rust SonarQube display formatters are unavailable")

        part = "آ"
        single = f"{part}😀 "
        self.assertIs(metric(single), single)
        self.assertIs(join([single]), single)
        self.assertEqual(metric("_first__متن_"), " first  متن ")
        self.assertEqual(metric(""), "")
        self.assertEqual(join(["", "آ\r😀", "", "tail\n"]), "\nآ\r😀\n\ntail\n")
        self.assertEqual(join([]), "")
        for label in (Derived("text"), b"bits", 1):
            self.assertIsNone(metric(label))
        for rows in ((single,), [Derived("a")], [2]):
            self.assertIsNone(join(rows))

    def test_all_surrogate_code_points_preserve_labels_and_condition_lines(self) -> None:
        try:
            module = import_module("_zulip_backend")
        except ImportError:
            self.skipTest("Wheel is absent")
        if getattr(module, "SONARQUBE_DISPLAY_API", None) != 1:
            self.skipTest("Wheel predates surrogate-safe SonarQube display")
        for code in range(0xD800, 0xE000):
            label = f"متن{chr(code)}\0😀"
            self.assertIs(module.format_sonarqube_metric_label(label), label)
            self.assertEqual(module.format_sonarqube_metric_label(f"_{label}__"), f" {label}  ")
            self.assertIs(module.join_sonarqube_condition_lines([label]), label)
            self.assertEqual(module.join_sonarqube_condition_lines(["", label, ""]), f"\n{label}\n")

    def test_existing_condition_selection_rendering_and_order(self) -> None:
        want = (
            "*  demo  متن : **other** 2 should be less than or equal to 4.\n"
            "* another 😀: **elevated**."
        )
        self.assertEqual(view.parse_conditions(synthetic_conditions()), want)
        self.assertEqual(view.parse_conditions(wrap_wild_value("conditions", [])), "")

    def test_real_callsite_native_dispatch_uses_only_prepared_values(self) -> None:
        metric = view._rust_format_sonarqube_metric_label
        join = view._rust_join_sonarqube_condition_lines
        enabled = os.environ.get("ZULIP_RUST_SONARQUBE_DISPLAY") == "1" or (
            supports_default_rust_backend()
            and getattr(view._rust_sonarqube_display, "SONARQUBE_DISPLAY_API", None) == 1
        )
        self.assertEqual(callable(metric), enabled)
        self.assertEqual(callable(join), enabled)
        expected_rows = [
            "*  demo  متن : **other** 2 should be less than or equal to 4.",
            "* another 😀: **elevated**.",
        ]
        self.assertEqual(view.parse_conditions(synthetic_conditions()), "\n".join(expected_rows))
        if metric is not None and join is not None:
            with (
                patch.object(
                    view, "_rust_format_sonarqube_metric_label", wraps=metric
                ) as label_spy,
                patch.object(view, "_rust_join_sonarqube_condition_lines", wraps=join) as join_spy,
            ):
                self.assertEqual(
                    view.parse_conditions(synthetic_conditions()), "\n".join(expected_rows)
                )
            self.assertEqual(
                [call.args for call in label_spy.call_args_list],
                [("_demo__متن_",), ("another_😀",)],
            )
            join_spy.assert_called_once_with(expected_rows)
        else:
            self.assertIsNone(metric)
            self.assertIsNone(join)

    def test_declines_malformed_and_legacy_error(self) -> None:
        self.assertEqual(view.parse_metric_name(Derived("not_used")), "override _")
        self.assertEqual(view.parse_metric_name("_\ud800_"), " \ud800 ")
        for fallback in (None, object(), lambda *args: None, lambda *args: ()):
            with (
                patch.object(view, "_rust_format_sonarqube_metric_label", fallback),
                patch.object(view, "_rust_join_sonarqube_condition_lines", fallback),
            ):
                self.assertEqual(view.parse_metric_name("_\ud800_"), " \ud800 ")
                self.assertIn("elevated", view.parse_conditions(synthetic_conditions()))

        with (
            patch.object(view, "parse_condition", return_value=1),
            self.assertRaisesRegex(TypeError, "expected str instance"),
        ):
            view.parse_conditions(synthetic_conditions())
