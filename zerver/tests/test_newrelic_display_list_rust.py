import os
from collections.abc import Iterator
from importlib import import_module
from unittest import TestCase
from unittest.mock import call, patch

from typing_extensions import override

from zerver.lib.native_backend import supports_default_rust_backend
from zerver.lib.validator import wrap_wild_value
from zerver.webhooks.newrelic import view


class NewRelicDisplayListRustTest(TestCase):
    def test_native_preserves_literal_bytes_and_singleton_identity(self) -> None:
        try:
            render = import_module("_zulip_backend").join_newrelic_display_list
        except (ImportError, AttributeError):
            self.skipTest("Rust New Relic display joining is unavailable")

        raw = chr(0xD800)
        self.assertIs(render([raw]), raw)
        self.assertEqual(render([]), "")
        self.assertEqual(render([" فارسی ", "😀\r\n", ""]), " فارسی , 😀\r\n, ")
        self.assertIsNone(render(["a", 1]))
        self.assertIsNone(render(("a", "b")))

    def test_prepared_lists_preserve_all_surrogate_code_points(self) -> None:
        try:
            module = import_module("_zulip_backend")
        except ImportError:
            self.skipTest("Wheel is absent")
        if getattr(module, "NEWRELIC_DISPLAY_LIST_API", None) != 1:
            self.skipTest("Wheel predates surrogate-safe New Relic list joining")
        render = module.join_newrelic_display_list
        for code in range(0xD800, 0xE000):
            text = f"عنوان{chr(code)}\0😀"
            self.assertIs(render([text]), text)
            self.assertEqual(render(["one", text, ""]), f"one, {text}, ")

    def test_condition_and_policy_receive_only_python_prepared_text(self) -> None:
        native = view._rust_join_newrelic_display_list
        enabled = os.environ.get("ZULIP_RUST_NEWRELIC_DISPLAY_LIST") == "1" or (
            supports_default_rust_backend()
            and getattr(view._rust_newrelic_display_list, "NEWRELIC_DISPLAY_LIST_API", None) == 1
        )
        self.assertEqual(callable(native), enabled)
        sample = wrap_wild_value(
            "synthetic",
            {
                "title": "demo",
                "priority": "LOW",
                "state": "CLOSED",
                "totalIncidents": 0,
                "alertConditionNames": [" فارسی ", "😀"],
                "alertPolicyNames": [],
            },
        )
        with (
            patch.object(view, "get_timestamp_string", return_value="synthetic time"),
            patch.object(view, "_rust_join_newrelic_display_list", wraps=native)
            if enabled
            else patch.object(view, "_rust_join_newrelic_display_list", None) as render,
        ):
            values = view.parse_payload(sample)
            self.assertEqual(
                values["conditions"],
                chr(96) + " فارسی " + chr(96) + ", " + chr(96) + "😀" + chr(96),
            )
            self.assertEqual(values["alert_policy"], "")
            if render is not None:
                render.assert_has_calls(
                    [call([chr(96) + " فارسی " + chr(96), chr(96) + "😀" + chr(96)]), call([])]
                )

    def test_custom_values_and_original_unsupported_text(self) -> None:
        native = view._rust_join_newrelic_display_list
        enabled = os.environ.get("ZULIP_RUST_NEWRELIC_DISPLAY_LIST") == "1" or (
            supports_default_rust_backend()
            and getattr(view._rust_newrelic_display_list, "NEWRELIC_DISPLAY_LIST_API", None) == 1
        )
        sample = wrap_wild_value(
            "synthetic",
            {"zulipCustomFields": {"detail": [None, 9, -0.0, "متن"], "invalid": {"literal": True}}},
        )
        with (
            patch.object(view, "_rust_join_newrelic_display_list", wraps=native)
            if enabled
            else patch.object(view, "_rust_join_newrelic_display_list", None) as render
        ):
            self.assertEqual(
                view.format_zulip_custom_fields(sample),
                "- **Detail**: None, 9, -0.0, متن\n- **Invalid**: *Value is not a supported data type*\n",
            )
            if render is not None:
                render.assert_called_once_with(["None", "9", "-0.0", "متن"])

    def test_fallback_preserves_original_subclass_order_and_errors(self) -> None:
        class ExactLegacy(list[str]):
            @override
            def __iter__(self) -> Iterator[str]:
                return iter(("special", "order"))

        with patch.object(view, "_rust_join_newrelic_display_list") as native:
            self.assertEqual(
                view._join_newrelic_display_list(ExactLegacy(["old"])), "special, order"
            )
            native.assert_not_called()

        for invalid in (None, (), False):
            with patch.object(view, "_rust_join_newrelic_display_list", return_value=invalid):
                raw = chr(0xD800)
                self.assertIs(view._join_newrelic_display_list([raw]), raw)
                self.assertEqual(view._join_newrelic_display_list([raw, "😀"]), raw + ", 😀")
