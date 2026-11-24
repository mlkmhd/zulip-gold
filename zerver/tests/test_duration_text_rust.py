import math
import os
from importlib import import_module
from unittest import TestCase
from unittest.mock import call, patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver import middleware


class DurationTextRustTest(TestCase):
    def test_fixed_point_rounding_and_decline(self) -> None:
        try:
            present = import_module("_zulip_backend").format_prepared_duration
        except (ImportError, AttributeError):
            self.skipTest("Rust duration text helper is not available")

        for value, expected in (
            (0.0, "0ms"),
            (-0.0, "-0ms"),
            (0.0005, "0ms"),
            (0.0015, "2ms"),
            (-0.0015, "-2ms"),
            (math.nextafter(1.0, 0.0), "1000ms"),
            (1.0, "1.0s"),
            (1.25, "1.2s"),
            (1.75, "1.8s"),
            (1_000_000.0, "1000000.0s"),
        ):
            self.assertEqual(present(value), expected)

        class Derived(float):
            pass

        for value in (
            math.nan,
            math.inf,
            -math.inf,
            1_000_001.0,
            -1_000_001.0,
            True,
            12,
            Derived(2.0),
        ):
            self.assertIsNone(present(value))


class DurationTextIntegrationTest(SimpleTestCase):
    def test_real_formatter_follows_startup_flag(self) -> None:
        enabled = os.environ.get("ZULIP_RUST_DURATION_TEXT") == "1"
        method = middleware._rust_format_prepared_duration
        if enabled:
            self.assertIsNotNone(method)
        else:
            self.assertIsNone(method)
        native_spy = (
            patch.object(middleware, "_rust_format_prepared_duration", wraps=method)
            if enabled
            else patch.object(middleware, "_rust_format_prepared_duration", None)
        )
        with native_spy as native:
            self.assertEqual(middleware.format_timedelta(0.017), "17ms")
            self.assertEqual(middleware.format_timedelta(1.25), "1.2s")
            if enabled:
                assert native is not None
                self.assertEqual(native.call_args_list, [call(0.017), call(1.25)])

    def test_unusual_float_and_integer_use_original_format(self) -> None:
        for value, expected in (
            (math.nan, "nanms"),
            (-math.inf, "-infms"),
            (math.inf, "infs"),
            (1_000_001.5, "1000001.5s"),
            (-1, "-1000ms"),
            (1, "1.0s"),
            (True, "1.0s"),
        ):
            self.assertEqual(middleware.format_timedelta(value), expected)

    def test_float_subclass_format_is_never_run_in_rust(self) -> None:
        seen: list[str] = []

        class CustomDuration(float):
            @override
            def __format__(self, format_spec: str) -> str:
                seen.append(format_spec)
                return "custom"

        self.assertEqual(middleware.format_timedelta(CustomDuration(2)), "customs")
        self.assertEqual(seen, [".1f"])
