import os
from importlib import import_module
from unittest import TestCase
from unittest.mock import patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import widget
from zerver.models import SubMessage


class WidgetTextRustTest(TestCase):
    def test_selected_plaintext(self) -> None:
        try:
            module = import_module("_zulip_backend")
            poll = module.extract_poll_widget_text
            todo = module.extract_todo_widget_text
        except (ImportError, AttributeError):
            self.skipTest("Rust widget text extraction is not available")

        self.assertEqual(poll(""), ("", []))
        self.assertEqual(todo(""), ("", []))
        self.assertEqual(
            poll("\r\n- a\u2028**x\v\u3000*\u001f b\x85*\r\u200b\n"),
            ("", ["a", "*x", "b", "\u200b"]),
        )
        self.assertEqual(
            todo(" title \u001c * Task: description: details\u001d - two\u001e- X:\tY\n"),
            ("title", [("Task", "description: details"), ("two", ""), ("X:\tY", "")]),
        )
        self.assertEqual(
            todo("\ufeff title\n- Task: \u00a0\n\u00a0*: remaining\n- Task: end"),
            ("\ufeff title", [("Task:", ""), ("", "remaining"), ("Task", "end")]),
        )

        class Derived(str):
            __slots__ = ()

        for invalid in (None, b"abc", Derived("title"), "\ud800", "title\n-\udcff"):
            self.assertIsNone(poll(invalid))
            self.assertIsNone(todo(invalid))


class WidgetTextIntegrationTest(SimpleTestCase):
    def test_real_selected_parsers_follow_startup_flag(self) -> None:
        enabled = os.environ.get("ZULIP_RUST_WIDGET_TEXT") == "1"
        poll = widget._extract_poll_widget_text
        todo = widget._extract_todo_widget_text
        if enabled:
            self.assertIsNotNone(poll)
            self.assertIsNotNone(todo)
        else:
            self.assertIsNone(poll)
            self.assertIsNone(todo)

        content = " پرسش \r\n\n -- یک \u2028* دو "
        plan = " عنوان \u001c- task: first: remainder\n* other: \n- last"
        poll_spy = (
            patch.object(widget, "_extract_poll_widget_text", wraps=poll)
            if enabled
            else patch.object(widget, "_extract_poll_widget_text", None)
        )
        todo_spy = (
            patch.object(widget, "_extract_todo_widget_text", wraps=todo)
            if enabled
            else patch.object(widget, "_extract_todo_widget_text", None)
        )
        with (
            poll_spy as called_poll,
            todo_spy as called_todo,
            patch.object(SubMessage, "save", side_effect=AssertionError("widget saved")),
            patch.object(
                widget, "do_widget_post_save_actions", side_effect=AssertionError("widget sent")
            ),
        ):
            self.assertEqual(
                widget.get_extra_data_from_widget_type(content, "poll"),
                widget.PollData(question="پرسش", options=["- یک", "دو"]),
            )
            self.assertEqual(
                widget.get_extra_data_from_widget_type(plan, "todo"),
                widget.TodoData(
                    task_list_title="عنوان",
                    tasks=[
                        widget.TodoTaskData(task="task", desc="first: remainder"),
                        widget.TodoTaskData(task="other:", desc=""),
                        widget.TodoTaskData(task="last", desc=""),
                    ],
                ),
            )
            if enabled:
                assert called_poll is not None
                assert called_todo is not None
                called_poll.assert_called_once_with(content)
                called_todo.assert_called_once_with(plan)

    def test_native_decline_keeps_python_string_semantics(self) -> None:
        class Derived(str):
            __slots__ = ()

            @override
            def splitlines(self, keepends: bool = False) -> list[str]:
                return ["custom title", "- task: desc"]

        supported = "head\n- row"
        subclass = Derived(supported)
        surrogate = "head\n- \ud800"
        self.assertEqual(
            widget.parse_poll_extra_data(subclass),
            widget.PollData(question="custom title", options=["task: desc"]),
        )
        self.assertEqual(
            widget.parse_todo_extra_data(subclass),
            widget.TodoData(
                task_list_title="custom title",
                tasks=[widget.TodoTaskData(task="task", desc="desc")],
            ),
        )
        self.assertEqual(
            widget.parse_poll_extra_data(surrogate),
            widget.PollData(question="head", options=["\ud800"]),
        )
        self.assertEqual(
            widget.parse_todo_extra_data(surrogate),
            widget.TodoData(
                task_list_title="head", tasks=[widget.TodoTaskData(task="\ud800", desc="")]
            ),
        )

    def test_pure_widget_data_shape_is_unchanged(self) -> None:
        with patch.object(SubMessage, "save", side_effect=AssertionError("widget saved")):
            self.assertEqual(
                widget.get_widget_data("/poll question\n- one\n* two"),
                ("poll", {"question": "question", "options": ["one", "two"]}),
            )
            self.assertEqual(
                widget.get_widget_data("/todo list\n- first: details"),
                (
                    "todo",
                    {"task_list_title": "list", "tasks": [{"task": "first", "desc": "details"}]},
                ),
            )
            self.assertEqual(widget.get_widget_data("/other text"), (None, None))
