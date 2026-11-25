import os
from importlib import import_module
from typing import Any
from unittest import TestCase
from unittest.mock import call, patch

from typing_extensions import override

from zerver.data_import import mattermost


class MattermostDisplayNameRustTest(TestCase):
    def test_existing_native_name_and_blankness(self) -> None:
        try:
            plan = import_module("_zulip_backend").plan_mattermost_display_name
        except (ImportError, AttributeError):
            self.skipTest("Rust Mattermost display helper is not available")

        self.assertEqual(plan("", ""), (" ", False))
        self.assertEqual(plan("\u001f\xa0", "\u2000\u3000"), ("\u001f\xa0 \u2000\u3000", False))
        self.assertEqual(plan(" فارسی ", " 😀"), (" فارسی   😀", True))
        self.assertEqual(plan("\u200b", ""), ("\u200b ", True))
        self.assertEqual(plan("", "\0"), (" \0", True))

        class Custom(str):
            __slots__ = ()

        for invalid in (b"arg", None, Custom("name"), "\ud800", 1):
            self.assertIsNone(plan(invalid, "other"))
            self.assertIsNone(plan("other", invalid))

    def test_real_helper_preserves_untrimmed_value_and_fallback_identity(self) -> None:
        native = mattermost._rust_plan_mattermost_display_name
        enabled = os.environ.get("ZULIP_RUST_MATTERMOST_DISPLAY_NAME") == "1"
        self.assertEqual(callable(native), enabled)
        display = f"synthetic {len(self.id())}"
        username = f"fallback {len(display)}"
        name_data = {"first_name": " فارسی ", "last_name": " 😀", "display_name": display}
        white_data = {"first_name": "\u001f", "last_name": "\u3000", "display_name": display}
        user_data = {"first_name": "", "last_name": "", "display_name": "", "username": username}
        with (
            patch.object(mattermost, "_rust_plan_mattermost_display_name", wraps=native)
            if enabled
            else patch.object(mattermost, "_rust_plan_mattermost_display_name", None) as plan
        ):
            self.assertEqual(mattermost._get_mattermost_display_name(name_data), " فارسی   😀")
            self.assertIs(mattermost._get_mattermost_display_name(white_data), display)
            self.assertIs(mattermost._get_mattermost_display_name(user_data), username)
            if plan is not None:
                self.assertEqual(
                    plan.call_args_list,
                    [call(" فارسی ", " 😀"), call("\u001f", "\u3000"), call("", "")],
                )

    def test_original_lookup_and_custom_format_evaluation_order(self) -> None:
        calls: list[str] = []

        class PreparedValue:
            def __init__(self, text: str) -> None:
                self.text = text

            @override
            def __format__(self, format_spec: str) -> str:
                self.assert_empty_format(format_spec)
                calls.append(f"format {self.text}")
                return self.text

            def assert_empty_format(self, spec: str) -> None:
                if spec:
                    raise AssertionError("Original format must stay empty")

        class Row(dict[str, Any]):
            @override
            def __getitem__(self, key: str) -> Any:
                calls.append(f"read {key}")
                return super().__getitem__(key)

        first = PreparedValue("first")
        last = PreparedValue("last")
        with patch.object(mattermost, "_rust_plan_mattermost_display_name") as native:
            self.assertEqual(
                mattermost._get_mattermost_display_name(Row(first_name=first, last_name=last)),
                "first last",
            )
            self.assertEqual(
                calls, ["read first_name", "read last_name", "format first", "format last"]
            )
            calls.clear()
            with self.assertRaisesRegex(KeyError, "last_name"):
                mattermost._get_mattermost_display_name(Row(first_name=first))
            self.assertEqual(calls, ["read first_name", "read last_name"])
            native.assert_not_called()

    def test_decline_malformed_and_surrogates_use_original_python(self) -> None:
        for invalid in (None, (), ["wrong", True], (True, True), ("wrong", 1)):
            with patch.object(
                mattermost, "_rust_plan_mattermost_display_name", return_value=invalid
            ):
                self.assertEqual(
                    mattermost._get_mattermost_display_name(
                        {"first_name": " متن ", "last_name": " x "}
                    ),
                    " متن   x ",
                )
        self.assertEqual(
            mattermost._get_mattermost_display_name({"first_name": "\ud800", "last_name": ""}),
            "\ud800 ",
        )
