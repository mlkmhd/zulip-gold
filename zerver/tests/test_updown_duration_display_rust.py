import os
from importlib import import_module
from random import Random
from typing import Any
from unittest import TestCase
from unittest.mock import patch

from zerver.lib.native_backend import supports_default_rust_backend
from zerver.lib.validator import wrap_wild_value
from zerver.webhooks.updown import view


class UpdownDurationDisplayRustTest(TestCase):
    def test_legacy_floor_duration_wording(self) -> None:
        try:
            format_duration = import_module("_zulip_backend").format_updown_duration_display
        except (ImportError, AttributeError):
            self.skipTest("Rust Updown duration display is not available")

        self.assertEqual(format_duration(0), "")
        self.assertEqual(format_duration(1), "1 second")
        self.assertEqual(format_duration(61), "1 minute 1 second")
        self.assertEqual(format_duration(90061), "1 day 1 hour 1 minute 1 second")
        self.assertEqual(format_duration(180122), "2 days 2 hours 2 minutes 2 seconds")
        self.assertEqual(format_duration(-1), "23 hours 59 minutes 59 seconds")
        self.assertEqual(format_duration(-86400), "")
        self.assertEqual(format_duration(-86399), "1 second")

        class Derived(int):
            pass

        for invalid in (None, "1", 1.0, True, Derived(1), -(2**63) - 1, 2**63):
            self.assertIsNone(format_duration(invalid))

    def test_signed_seconds_match_at_boundaries_and_across_i64(self) -> None:
        try:
            native = import_module("_zulip_backend").format_updown_duration_display
        except (ImportError, AttributeError):
            self.skipTest("Rust Updown duration display is not available")

        random = Random(834219)
        minimum, maximum = -(2**63), 2**63 - 1
        samples = [
            multiplier * unit + offset
            for unit in (1, 60, 3600, 86400)
            for multiplier in (-100, -2, -1, 0, 1, 2, 100)
            for offset in (-2, -1, 0, 1, 2)
        ]
        samples += [minimum, minimum + 1, maximum - 1, maximum]
        samples += [random.randint(minimum, maximum) for _ in range(4096)]
        with patch.object(view, "_rust_format_updown_duration_display", None):
            for value in samples:
                self.assertEqual(
                    native(value), view.get_time_string_based_on_duration(value), value
                )

    def test_existing_presentation_exact_for_signed_seconds(self) -> None:
        samples = [
            (0, ""),
            (1, "1 second"),
            (3601, "1 hour 1 second"),
            (3661, "1 hour 1 minute 1 second"),
            (172802, "2 days 2 seconds"),
            (-1, "23 hours 59 minutes 59 seconds"),
            (-3600, "23 hours"),
            (-86400, ""),
            (-86399, "1 second"),
        ]
        for seconds, expected in samples:
            with self.subTest(seconds=seconds):
                self.assertEqual(view.get_time_string_based_on_duration(seconds), expected)

    def test_existing_formatter_passes_exact_seconds_only(self) -> None:
        native = view._rust_format_updown_duration_display
        enabled = os.environ.get("ZULIP_RUST_UPDOWN_DURATION_DISPLAY") == "1" or (
            supports_default_rust_backend()
            and getattr(view._rust_updown_duration, "UPDOWN_DURATION_DISPLAY_API", None) == 1
        )
        self.assertEqual(callable(native), enabled)
        if native is not None:
            with patch.object(view, "_rust_format_updown_duration_display", wraps=native) as spy:
                self.assertEqual(
                    view.get_time_string_based_on_duration(-1), "23 hours 59 minutes 59 seconds"
                )
            spy.assert_called_once_with(-1)
        else:
            self.assertEqual(
                view.get_time_string_based_on_duration(-1), "23 hours 59 minutes 59 seconds"
            )

    def test_old_decline_malformed_and_legacy_types(self) -> None:
        samples: list[Any] = [0, True, 2**78, -(2**78), 1.5, DerivedInt(1)]
        with patch.object(view, "_rust_format_updown_duration_display", None):
            expected = [view.get_time_string_based_on_duration(seconds) for seconds in samples]
        self.assertEqual(expected[0:2], ["", "1 second"])
        self.assertEqual(expected[-2:], ["1.5 seconds", "1 second"])
        self.assertEqual(
            [view.get_time_string_based_on_duration(seconds) for seconds in samples], expected
        )
        for fallback in (None, object(), lambda *args: None, lambda *args: ()):
            with patch.object(view, "_rust_format_updown_duration_display", fallback):
                self.assertEqual(
                    [view.get_time_string_based_on_duration(seconds) for seconds in samples],
                    expected,
                )
        with (
            patch.object(
                view, "add_time_part_to_string_date_if_needed", side_effect=["", "", "", "legacy "]
            ) as formatter,
            patch.object(view, "_rust_format_updown_duration_display") as spy,
        ):
            self.assertEqual(view.get_time_string_based_on_duration(1), "legacy")
            self.assertEqual(
                [call.args for call in formatter.call_args_list],
                [(0, "day"), (0, "hour"), (0, "minute"), (1, "second")],
            )
            spy.assert_not_called()
        invalid: Any = "not an int"
        with self.assertRaisesRegex(TypeError, "unsupported operand"):
            view.get_time_string_based_on_duration(invalid)

    def test_existing_up_display_body_keeps_selection(self) -> None:
        for started, seconds, expected in [
            (None, 61, "Service is `up`."),
            ("fixture", 61, "Service is `up` again after 1 minute 1 second."),
            ("fixture", 0, "Service is `up` again."),
            ("fixture", -86400, "Service is `up` again."),
            ("fixture", -1, "Service is `up` again after 23 hours 59 minutes 59 seconds."),
        ]:
            with self.subTest(started=started, seconds=seconds):
                event = wrap_wild_value(
                    "fixture", {"downtime": {"started_at": started, "duration": seconds}}
                )
                self.assertEqual(view.get_body_for_up_event(event), expected)


class DerivedInt(int):
    pass
