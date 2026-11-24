import os
from importlib import import_module
from typing import override
from unittest import TestCase
from unittest.mock import call, patch

from zerver.lib.validator import wrap_wild_value
from zerver.webhooks.gong import view


class GongDisplayRustTest(TestCase):
    def test_duration_native_edges(self) -> None:
        try:
            native = import_module("_zulip_backend").format_gong_duration_display
        except (ImportError, AttributeError):
            self.skipTest("Rust Gong display is not available")

        for seconds, expected in [
            (-(2**63), "-9223372036854775808 seconds"),
            (-1, "-1 seconds"),
            (0, "0 seconds"),
            (1, "1 second"),
            (59, "59 seconds"),
            (60, "1 minute"),
            (89, "1 minute"),
            (90, "2 minutes"),
            (3569, "59 minutes"),
            (3570, "1 hour"),
            (3630, "1 hour 1 minute"),
            (7170, "2 hours"),
            (2**63 - 1, "2562047788015215 hours 30 minutes"),
        ]:
            self.assertEqual(native(seconds), expected)

        for invalid in (True, 1.0, "1", -(2**63) - 1, 2**63):
            self.assertIsNone(native(invalid))

    def test_native_topic_text_is_literal(self) -> None:
        try:
            native = import_module("_zulip_backend").join_gong_topic_display
        except (ImportError, AttributeError):
            self.skipTest("Rust Gong display is not available")

        self.assertEqual(native(["", " فارسی\r\n😀 "], 1), ",  فارسی\r\n😀  and 1 more topic.")
        self.assertEqual(native(["one", "two"], 2), "one, two and 2 more topics.")
        self.assertEqual(native([], 1), " and 1 more topic.")
        self.assertEqual(native([""], 0), "")
        self.assertEqual(native(["", ""], -2), ", .")
        self.assertEqual(native([], 0), "")
        self.assertIsNone(native(["\ud800"], 1))
        self.assertIsNone(native((["a"],), 1))
        self.assertIsNone(native(["a"], True))
        self.assertIsNone(native(["a"], 2**63))

    def test_real_topics_keep_order_and_only_pass_prepared_data(self) -> None:
        duration = view._rust_format_gong_duration_display
        join = view._rust_join_gong_topic_display
        enabled = os.environ.get("ZULIP_RUST_GONG_DISPLAY") == "1"
        self.assertEqual(callable(duration), enabled)
        self.assertEqual(callable(join), enabled)
        data = [
            {"name": "outside", "duration": 1},
            {"name": "اول", "duration": 90},
            {"name": "never used", "duration": -1},
            {"name": "دوم", "duration": 90},
            {"name": "🌋", "duration": 60},
        ]
        with (
            patch.object(view, "_rust_format_gong_duration_display", wraps=duration)
            if enabled
            else patch.object(view, "_rust_format_gong_duration_display", None) as native_duration,
            patch.object(view, "_rust_join_gong_topic_display", wraps=join)
            if enabled
            else patch.object(view, "_rust_join_gong_topic_display", None) as native_join,
        ):
            self.assertEqual(
                view.get_topics(wrap_wild_value("synthetic_topics", data)),
                "اول (2 minutes), دوم (2 minutes), 🌋 (1 minute) and 2 more topics.",
            )
            if native_duration is not None:
                self.assertEqual(native_duration.call_args_list, [call(90), call(90), call(60)])
            if native_join is not None:
                native_join.assert_called_once_with(
                    ["اول (2 minutes)", "دوم (2 minutes)", "🌋 (1 minute)"], 2
                )
        self.assertEqual(view.get_topics(wrap_wild_value("synthetic_topics", [])), "")

    def test_real_python_fallback_and_subclass_dispatch(self) -> None:
        class LegacySeconds(int):
            @override
            def __format__(self, format_spec: str) -> str:
                return "legacy"

        for fallback in (None, (), False):
            with (
                patch.object(
                    view, "_rust_format_gong_duration_display", return_value=fallback
                ) as duration,
                patch.object(view, "_rust_join_gong_topic_display", return_value=fallback) as join,
            ):
                self.assertEqual(view.format_duration_to_string(LegacySeconds(1)), "legacy second")
                duration.assert_called_once_with(1)
                self.assertEqual(
                    view.get_topics(
                        wrap_wild_value("synthetic_topics", [{"name": "نهایی", "duration": 0}])
                    ),
                    "نهایی.",
                )
                join.assert_called_once_with(["نهایی"], -2)
        self.assertEqual(view.format_duration_to_string(LegacySeconds(1)), "legacy second")
