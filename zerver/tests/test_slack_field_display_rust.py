import os
from importlib import import_module
from unittest import TestCase
from unittest.mock import Mock, patch

from typing_extensions import override

from zerver.data_import import slack_message_conversion as converter
from zerver.lib.validator import wrap_wild_value


class SlackFieldDisplayRustTest(TestCase):
    def test_native_prepared_table_and_plain_join(self) -> None:
        try:
            native = import_module("_zulip_backend")
            table = native.assemble_prepared_slack_section_table
            join = native.join_prepared_slack_attachment_fields
        except (AttributeError, ImportError):
            self.skipTest("Rust Slack field display is not available")

        first = "tag:متن🌐".removeprefix("tag:")
        self.assertIs(join([first]), first)
        self.assertEqual(join(["", "  a\r", "b\n", ""]), "\n  a\r\nb\n\n")
        self.assertEqual(join([]), "")
        self.assertEqual(table([]), "| | |\n|-|-|\n")
        self.assertEqual(
            table(["آ\r", "second", "  🌐"]),
            "| | |\n|-|-|\n| آ\r | second |\n|   🌐 |  |\n",
        )
        self.assertIsNone(table(["not cleaned\n"]))
        self.assertIsNone(table(["not | cleaned"]))
        self.assertIsNone(table(["\ud800"]))
        self.assertIsNone(join(["ok", "\ud800"]))
        self.assertIsNone(join((first,)))

    def test_section_cleanup_and_single_field_bypass_are_python_owned(self) -> None:
        native = converter._rust_assemble_prepared_slack_section_table
        if os.environ.get("ZULIP_RUST_SLACK_FIELD_DISPLAY") != "1":
            self.assertIsNone(native)
        spy = Mock(wraps=native) if native is not None else None
        block = {
            "type": "section",
            "fields": [
                {"type": "plain_text", "text": "A|\nB"},
                {"type": "mrkdwn", "text": "right\r"},
                {"type": "plain_text", "text": "🌐\u2028end"},
            ],
        }
        singleton = {"type": "section", "fields": [{"type": "plain_text", "text": "A|\nB"}]}
        with patch.object(converter, "_rust_assemble_prepared_slack_section_table", spy):
            self.assertEqual(
                converter.render_block(wrap_wild_value("synthetic", block)),
                "| | |\n|-|-|\n| A  B | right\r |\n| 🌐\u2028end |  |",
            )
            self.assertEqual(
                converter.render_block(wrap_wild_value("synthetic", singleton)), "A|\nB"
            )
        if spy is not None:
            spy.assert_called_once_with(["A  B", "right\r", "🌐\u2028end"])

    def test_attachment_only_joins_original_preformatted_rows(self) -> None:
        native = converter._rust_join_prepared_slack_attachment_fields
        if os.environ.get("ZULIP_RUST_SLACK_FIELD_DISPLAY") != "1":
            self.assertIsNone(native)
        spy = Mock(wraps=native) if native is not None else None
        with patch.object(converter, "_rust_join_prepared_slack_attachment_fields", spy):
            actual = converter.render_attachment(
                wrap_wild_value(
                    "synthetic",
                    {
                        "fields": [
                            {"title": "one", "value": "uno\n\tdos"},
                            {"value": "آ"},
                            {"title": "third", "value": ""},
                        ]
                    },
                )
            )
        self.assertEqual(actual, "*one*: uno\n\tdos\nآ\n*third*")
        if spy is not None:
            spy.assert_called_once_with(["*one*: uno\n\tdos", "آ", "*third*"])

    def test_decline_and_exotic_strings_keep_python_operators(self) -> None:
        class Custom(str):
            __slots__ = ()

            @override
            def __format__(self, format_spec: str) -> str:
                return "custom"

        raw = Custom("original")
        with (
            patch.object(
                converter, "_rust_assemble_prepared_slack_section_table", return_value=None
            ) as table,
            patch.object(
                converter, "_rust_join_prepared_slack_attachment_fields", return_value=None
            ) as join,
        ):
            self.assertEqual(
                converter._assemble_section_table([raw]), "| | |\n|-|-|\n| custom |  |\n"
            )
            self.assertEqual(converter._join_attachment_fields([raw, "\ud800"]), "original\n\ud800")
        table.assert_called_once_with([raw])
        join.assert_called_once_with([raw, "\ud800"])
