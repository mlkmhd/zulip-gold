import os
from importlib import import_module
from unittest import TestCase
from unittest.mock import call, patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.data_import import slack_message_conversion
from zerver.lib.validator import wrap_wild_value


class SlackImportPiecesRustTest(TestCase):
    def test_trim_drop_join_and_identity(self) -> None:
        try:
            join = import_module("_zulip_backend").join_slack_import_pieces
        except (ImportError, AttributeError):
            self.skipTest("Rust Slack piece assembly is unavailable")

        first = "value:فارسی😀".removeprefix("value:")
        self.assertIs(join(["\u001f", first, " "], "\n"), first)
        pieces = ["\u001f A\t  B\xa0", "", "\r\n", " Ελληνικά\u0085", "\u200b\ufeff"]
        self.assertEqual(join(pieces, "\n"), "A\t  B\nΕλληνικά\n\u200b\ufeff")
        self.assertEqual(join(pieces, "\n\n"), "A\t  B\n\nΕλληνικά\n\n\u200b\ufeff")
        self.assertEqual(join(["\r", " "], "\n"), "")
        self.assertIsNone(join(["a", "b"], "|"))
        self.assertIsNone(join(("a", "b"), "\n"))
        self.assertIsNone(join(["a", "\ud800"], "\n"))


class SlackImportPiecesIntegrationTest(SimpleTestCase):
    def test_actual_prepared_join_sites_and_flag(self) -> None:
        converter = slack_message_conversion
        enabled = os.environ.get("ZULIP_RUST_SLACK_IMPORT_PIECES") == "1"
        method = converter._rust_join_slack_import_pieces
        if enabled:
            self.assertIsNotNone(method)
        else:
            self.assertIsNone(method)
        native_spy = (
            patch.object(converter, "_rust_join_slack_import_pieces", wraps=method)
            if enabled
            else patch.object(converter, "_rust_join_slack_import_pieces", None)
        )
        context = {
            "type": "context",
            "elements": [
                {"type": "plain_text", "text": " left\xa0"},
                {"type": "mrkdwn", "text": " فارسی "},
            ],
        }
        section = {
            "type": "section",
            "text": {"type": "plain_text", "text": "\t Title"},
            "fields": [{"type": "mrkdwn", "text": " Field\u001f"}],
        }
        attachment = {"pretext": " lead ", "text": " body\r\n"}
        with native_spy as native:
            self.assertEqual(
                converter.render_block(wrap_wild_value("synthetic", context)), "left\n\nفارسی"
            )
            self.assertEqual(
                converter.render_block(wrap_wild_value("synthetic", section)), "Title\n\nField"
            )
            self.assertEqual(
                converter.render_attachment(wrap_wild_value("synthetic", attachment)),
                "lead\n\nbody",
            )
            self.assertEqual(
                converter.process_slack_block_and_attachment(
                    wrap_wild_value("synthetic", {"text": " \t fallback  "})
                ),
                "fallback",
            )
            if enabled:
                assert native is not None
                self.assertEqual(
                    native.call_args_list,
                    [
                        call([" left\xa0", " فارسی "], "\n\n"),
                        call(["\t Title", " Field\u001f"], "\n\n"),
                        call([" lead ", " body\r\n"], "\n\n"),
                        call([" \t fallback  "], "\n"),
                    ],
                )

    def test_synthetic_block_and_attachment_keep_distinct_separators(self) -> None:
        message = wrap_wild_value(
            "synthetic",
            {
                "text": "unused raw fallback",
                "blocks": [
                    {
                        "type": "context",
                        "elements": [
                            {"type": "plain_text", "text": " First "},
                            {"type": "plain_text", "text": " Second "},
                        ],
                    }
                ],
                "attachments": [{"pretext": " Third ", "text": " Fourth "}],
            },
        )
        self.assertEqual(
            slack_message_conversion.process_slack_block_and_attachment(message),
            "First\n\nSecond\nThird\n\nFourth",
        )

    def test_decline_keeps_exact_python_strip_call_order(self) -> None:
        calls: list[str] = []

        class CustomText(str):
            __slots__ = ()

            @override
            def strip(self, chars: str | None = None) -> str:
                calls.append("strip")
                return "present" if len(calls) == 1 else "actual"

        self.assertEqual(
            slack_message_conversion._join_prepared_pieces([CustomText("ignored")], "\n"),
            "actual",
        )
        self.assertEqual(calls, ["strip", "strip"])
        self.assertEqual(
            slack_message_conversion._join_prepared_pieces([" a ", "\ud800 "], "\n"),
            "a\n\ud800",
        )
