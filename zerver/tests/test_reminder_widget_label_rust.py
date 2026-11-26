from typing import SupportsIndex
from unittest.mock import patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import reminders


class ReminderWidgetLabelContractTest(SimpleTestCase):
    def test_old_prefix_rules_are_not_a_full_command_parser(self) -> None:
        for body, expected in (
            ("/poll", "poll"),
            ("/poller\ud800", "poll"),
            ("/todo list", "todo list"),
            ("/todo\udfff", "todo list"),
            (" /poll", None),
            ("/Poll Q", None),
            ("/pol\ud800l", None),
            ("", None),
        ):
            with self.subTest(body=body):
                self.assertEqual(reminders._reminder_widget_label(body), expected)

    def test_current_wheel_receives_only_the_existing_prefix(self) -> None:
        backend = reminders._rust_reminder_widget_label
        if getattr(backend, "REMINDER_WIDGET_LABEL_API", None) != 1:
            self.skipTest("Matching Rust reminder widget label contract is not available")
        with patch.object(
            backend, "reminder_widget_label", wraps=backend.reminder_widget_label
        ) as select:
            self.assertEqual(reminders._reminder_widget_label("/todo finish testing"), "todo list")
        select.assert_called_once_with("/todo")

    def test_custom_startswith_still_runs(self) -> None:
        class Custom(str):
            __slots__ = ()

            @override
            def startswith(
                self,
                prefix: str | tuple[str, ...],
                start: SupportsIndex | None = None,
                end: SupportsIndex | None = None,
                /,
            ) -> bool:
                return prefix == "/poll"

        with patch.object(reminders, "_rust_reminder_context", None):
            self.assertEqual(reminders._reminder_widget_label(Custom("ordinary")), "poll")
