import os
from importlib import import_module
from unittest import TestCase
from unittest.mock import call, patch

from zerver.webhooks.openproject import view


class OpenProjectDurationDisplayRustTest(TestCase):
    def test_native_literal_parts_and_valid_shape_only(self) -> None:
        try:
            module = import_module("_zulip_backend")
            part = module.format_openproject_duration_part
            join = module.join_openproject_duration_parts
        except (ImportError, AttributeError):
            self.skipTest("Rust OpenProject duration display is not available")

        self.assertEqual(part(1, "hour"), "1 hour")
        self.assertEqual(part(0, "seconds"), "0 seconds")
        self.assertEqual(part(-(2**63), "متن"), "-9223372036854775808 متن")
        self.assertIsNone(part(2**63, "days"))
        self.assertIsNone(part(True, "day"))
        self.assertIsNone(part(1, "\ud800"))
        self.assertEqual(join(["1 day", "2 minutes"]), "1 day and 2 minutes")
        self.assertEqual(join(["", "متن\n", "🤝"]), ", متن\n and 🤝")
        self.assertIsNone(join([]))
        self.assertIsNone(join(["one"]))
        self.assertIsNone(join(("one", "two")))
        self.assertIsNone(join(["one", "\ud800"]))

    def test_real_host_parser_and_duration_display_dispatch(self) -> None:
        part = view._rust_format_openproject_duration_part
        join = view._rust_join_openproject_duration_parts
        enabled = os.environ.get("ZULIP_RUST_OPENPROJECT_DURATION_DISPLAY") == "1"
        self.assertEqual(callable(part), enabled)
        self.assertEqual(callable(join), enabled)
        with (
            patch.object(view, "int", create=True, side_effect=int) as convert,
            patch.object(view, "_rust_format_openproject_duration_part", wraps=part)
            if enabled
            else patch.object(view, "_rust_format_openproject_duration_part", None) as native_part,
            patch.object(view, "_rust_join_openproject_duration_parts", wraps=join)
            if enabled
            else patch.object(view, "_rust_join_openproject_duration_parts", None) as native_join,
        ):
            self.assertEqual(
                view.format_duration("P1DT2H٣M٠S"), "1 day, 2 hours, 3 minutes and 0 seconds"
            )
            self.assertEqual(
                convert.call_args_list,
                [call(c) for c in ("1", "1", "2", "2", "٣", "٣", "٠", "٠")],
            )
            if native_part is not None:
                self.assertEqual(
                    native_part.call_args_list,
                    [call(1, "day"), call(2, "hours"), call(3, "minutes"), call(0, "seconds")],
                )
            if native_join is not None:
                native_join.assert_called_once_with(["1 day", "2 hours", "3 minutes", "0 seconds"])
        self.assertEqual(view.format_duration("PT١S"), "1 second")
        self.assertEqual(view.format_duration("P0DT0001H"), "0 days and 1 hour")

    def test_original_errors_and_overflow_fallback(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid ISO 8601 duration format: P-1D"):
            view.format_duration("P-1D")
        for empty in ("P", "PT"):
            with self.assertRaisesRegex(IndexError, "list index out of range"):
                view.format_duration(empty)
        amount = 2**90
        self.assertEqual(view.format_duration(f"P{amount}D"), f"{amount} days")

    def test_decline_and_malformed_wheels_preserve_original_wording(self) -> None:
        for unsupported in (None, False, ()):
            with (
                patch.object(
                    view, "_rust_format_openproject_duration_part", return_value=unsupported
                ) as part,
                patch.object(
                    view, "_rust_join_openproject_duration_parts", return_value=unsupported
                ) as join,
            ):
                self.assertEqual(view.format_duration("PT01H٠٢M"), "1 hour and 2 minutes")
                self.assertEqual(part.call_args_list, [call(1, "hour"), call(2, "minutes")])
                join.assert_called_once_with(["1 hour", "2 minutes"])
