from unittest import TestCase
from unittest.mock import Mock, patch

from typing_extensions import override

from zerver.lib.webhooks import git


class GitTagEventRustTest(TestCase):
    def test_tag_link_punctuation_and_surrogate_output(self) -> None:
        url = "https://example.test/t/🌱"
        self.assertEqual(
            git.get_push_tag_event_message("آزمایشی", "نسخه؟", url, "deleted"),
            f"آزمایشی deleted tag [نسخه؟]({url}).",
        )
        self.assertEqual(
            git.get_push_tag_event_message("Name", "release!", ""), "Name pushed tag release!"
        )
        self.assertEqual(
            git.get_push_tag_event_message("x\ud800", "demo\udfff", "path\ud800", "act\udc00"),
            "x\ud800 act\udc00 tag [demo\udfff](path\ud800).",
        )
        with self.assertRaisesRegex(IndexError, "^string index out of range$"):
            git.get_push_tag_event_message("Name", "")

    def test_custom_template_and_str_subclass_still_control_behavior(self) -> None:
        native = Mock(side_effect=AssertionError("changed template may not take the native path"))
        with (
            patch.object(git, "_rust_format_prepared_tag_event", native),
            patch.object(git, "TAG_WITHOUT_URL_TEMPLATE", "<{tag_name}>"),
        ):
            self.assertEqual(git.get_push_tag_event_message("Name", "a"), "Name pushed tag <a>.")
        native.assert_not_called()

        class Tag(str):
            __slots__ = ()

            @override
            def __format__(self, format_spec: str) -> str:
                return "custom"

        self.assertEqual(git.get_push_tag_event_message("Name", Tag("!")), "Name pushed tag custom")

    def test_actual_formatter_uses_current_native_extension(self) -> None:
        format_event = git._rust_format_prepared_tag_event
        if format_event is None:
            self.skipTest("requires the current native tag formatter")
        with patch.object(git, "_rust_format_prepared_tag_event", wraps=format_event) as render:
            self.assertEqual(git.get_push_tag_event_message("Name", "1"), "Name pushed tag 1.")
        render.assert_called_once_with("Name", "1", None, "pushed")
