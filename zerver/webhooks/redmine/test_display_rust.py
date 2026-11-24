import os
from importlib import import_module
from typing import override
from unittest import TestCase
from unittest.mock import patch

from zerver.lib.validator import check_string, wrap_wild_value
from zerver.webhooks.redmine import view


class RedmineDisplayRustTest(TestCase):
    def test_native_literal_text_and_input_shapes(self) -> None:
        try:
            module = import_module("_zulip_backend")
            label = module.format_redmine_display_issue_label
            join = module.join_redmine_display_name
        except (ImportError, AttributeError):
            self.skipTest("Rust Redmine display helpers are unavailable")

        self.assertEqual(label(-(2**63), "متن \t😀"), "#-9223372036854775808 متن \t😀")
        self.assertEqual(label(3, ""), "#3 ")
        self.assertIsNone(label(True, "word"))
        self.assertIsNone(label(2**70, "word"))
        self.assertIsNone(label(3, "\ud800"))
        self.assertEqual(join("", ""), " ")
        self.assertEqual(join(" فارسی ", "😀 \0"), " فارسی  😀 \0")
        self.assertIsNone(join("\ud800", "word"))
        self.assertIsNone(join("word", None))

    def test_real_ready_values_dispatch_exactly(self) -> None:
        native_label = view._rust_format_redmine_display_issue_label
        native_join = view._rust_join_redmine_display_name
        enabled = os.environ.get("ZULIP_RUST_REDMINE_DISPLAY") == "1"
        self.assertEqual(callable(native_label), enabled)
        self.assertEqual(callable(native_join), enabled)
        with (
            patch.object(view, "_rust_format_redmine_display_issue_label", wraps=native_label)
            if enabled
            else patch.object(view, "_rust_format_redmine_display_issue_label", None) as label,
            patch.object(view, "_rust_join_redmine_display_name", wraps=native_join)
            if enabled
            else patch.object(view, "_rust_join_redmine_display_name", None) as join,
        ):
            self.assertEqual(
                view._extract_issue_label(
                    wrap_wild_value("item", {"id": 42, "subject": " فارسی 😀 "})
                ),
                "#42  فارسی 😀 ",
            )
            self.assertEqual(
                view._get_user_name(
                    wrap_wild_value("name", {"firstname": " الف ", "lastname": " ب"})
                ),
                " الف   ب",
            )
            if label is not None:
                label.assert_called_once_with(42, " فارسی 😀 ")
            if join is not None:
                join.assert_called_once_with(" الف ", " ب")

    def test_custom_first_name_formats_before_second_field(self) -> None:
        events: list[str] = []

        class LegacyText(str):
            __slots__ = ()

            @override
            def __format__(self, format_spec: str) -> str:
                events.append(f"format {self!s}")
                return str(self)

        def tame(name: str, prepared: object) -> str:
            events.append(name)
            return check_string(name, prepared)

        with (
            patch.object(view, "check_string", side_effect=tame),
            patch.object(view, "_rust_join_redmine_display_name") as native,
        ):
            self.assertEqual(
                view._get_user_name(
                    wrap_wild_value(
                        "name", {"firstname": LegacyText("first"), "lastname": LegacyText("last")}
                    )
                ),
                "first last",
            )
            native.assert_not_called()
        self.assertEqual(
            events, ["name['firstname']", "format first", "name['lastname']", "format last"]
        )

    def test_absent_and_declining_paths_keep_unusual_python_values(self) -> None:
        for unsupported in (None, (), False):
            with (
                patch.object(
                    view, "_rust_format_redmine_display_issue_label", return_value=unsupported
                ) as label,
                patch.object(
                    view, "_rust_join_redmine_display_name", return_value=unsupported
                ) as join,
            ):
                self.assertEqual(
                    view._extract_issue_label(
                        wrap_wild_value("item", {"id": 1, "subject": "\ud800"})
                    ),
                    "#1 \ud800",
                )
                self.assertEqual(
                    view._get_user_name(
                        wrap_wild_value("name", {"firstname": "A", "lastname": "\ud800"})
                    ),
                    "A \ud800",
                )
                label.assert_called_once_with(1, "\ud800")
                join.assert_called_once_with("A", "\ud800")
        for number in (2**90, True):
            self.assertEqual(
                view._extract_issue_label(
                    wrap_wild_value("item", {"id": number, "subject": "literal"})
                ),
                f"#{number} literal",
            )
