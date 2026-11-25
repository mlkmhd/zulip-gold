import json
import os
from importlib import import_module
from typing import SupportsIndex
from unittest import TestCase
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import widget
from zerver.lib.message import SendMessageRequest
from zerver.models import Message, SubMessage


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

        self.assertEqual(poll(Derived("title")), ("title", []))
        self.assertEqual(todo(Derived("title")), ("title", []))
        for invalid in (None, b"abc"):
            self.assertIsNone(poll(invalid))
            self.assertIsNone(todo(invalid))

    def test_native_subclass_callbacks_and_dynamic_rows(self) -> None:
        try:
            module = import_module("_zulip_backend")
        except ImportError:
            self.skipTest("Rust widget text extraction is not available")

        calls = []

        class Title(str):
            __slots__ = ()

            @override
            def strip(self, chars: str | None = None, /) -> str:
                calls.append("title.strip")
                return "unmodified title"

        class Row(str):
            __slots__ = ()

            @override
            def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
                raise AssertionError("a str subclass must not intercept native encoding")

            @override
            def strip(self, chars: str | None = None, /) -> str:
                calls.append("row.strip")
                return Row(" \t - \ud800: description  ")

        class Derived(str):
            __slots__ = ()

            @override
            def splitlines(self, keepends: bool = False) -> list[str]:
                calls.append("splitlines")
                return [Title("title"), Row("option")]

        text = Derived("ignored")
        for fn, expected in (
            (module.extract_poll_widget_text, ("unmodified title", ["\ud800: description  "])),
            (module.extract_todo_widget_text, ("unmodified title", [("\ud800", "description")])),
        ):
            calls.clear()
            self.assertEqual(fn(text), expected)
            self.assertEqual(calls, ["splitlines", "title.strip", "row.strip"])


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


class WidgetShorthandRustTest(SimpleTestCase):
    def test_exact_dispatch_under_startup_flag(self) -> None:
        enabled = os.environ.get("ZULIP_RUST_WIDGET_SHORTHAND") == "1"
        native = widget._parse_widget_shorthand
        self.assertEqual(callable(native), enabled)
        examples = [
            ("", (None, None)),
            (" /poll title", (None, None)),
            ("/polling title", (None, None)),
            ("/me /todo", (None, None)),
            ("/POLL", (None, None)),
            ("/poll\u200b title", (None, None)),
            ("/todo\ufeff", (None, None)),
            ("/poll", ("poll", {"question": "", "options": []})),
            (
                "/poll\u001fپرسش\r\n -- گزینه\u2028* second",
                ("poll", {"question": "پرسش", "options": ["- گزینه", "second"]}),
            ),
            (
                "/todo\x85- task: first: last\u001c* : empty title",
                (
                    "todo",
                    {
                        "task_list_title": "",
                        "tasks": [
                            {"task": "task", "desc": "first: last"},
                            {"task": "", "desc": "empty title"},
                        ],
                    },
                ),
            ),
        ]
        with (
            patch.object(SubMessage, "save", side_effect=AssertionError("widget saved")),
            patch.object(
                widget,
                "get_extra_data_from_widget_type",
                wraps=widget.get_extra_data_from_widget_type,
            ) as legacy,
        ):
            for content, expected in examples:
                with self.subTest(content=content):
                    actual = widget.get_widget_data(content)
                    self.assertEqual(json.dumps(actual), json.dumps(expected))

        self.assertEqual(legacy.call_count, 0 if enabled else 3)
        if enabled:
            self.assertIsNotNone(native)
            assert native is not None
            self.assertEqual(native("/other"), (None, None))
            self.assertIsNone(native(b"/todo"))
            self.assertEqual(native("/poll\ud800"), (None, None))

    def test_native_result_preserves_surrogates_and_private_use_characters(self) -> None:
        try:
            module = import_module("_zulip_backend")
            parse = module.parse_widget_shorthand
            poll = module.extract_poll_widget_text
            todo = module.extract_todo_widget_text
        except (ImportError, AttributeError):
            self.skipTest("Rust widget parsing is not available")

        prefix = "\U000f0000\U000f0001"
        for point in range(0xD800, 0xE000):
            tail = prefix + chr(point)
            source = f" {tail} \r\n - {tail}: {tail}\u2028* \u3000{tail}"
            options = [f"{tail}: {tail}", tail]
            tasks = [(tail, tail), (tail, "")]
            with self.subTest(code_point=point):
                self.assertEqual(poll(source), (tail, options))
                self.assertEqual(todo(source), (tail, tasks))
                self.assertEqual(
                    parse("/poll" + source), ("poll", {"question": tail, "options": options})
                )
                self.assertEqual(
                    parse("/todo" + source),
                    (
                        "todo",
                        {
                            "task_list_title": tail,
                            "tasks": [{"task": task, "desc": desc} for task, desc in tasks],
                        },
                    ),
                )
        self.assertEqual(poll("\ud800\udc00\n- " + prefix), ("\ud800\udc00", [prefix]))

    def test_native_result_matches_python_across_whitespace_and_rows(self) -> None:
        try:
            parse = import_module("_zulip_backend").parse_widget_shorthand
        except (ImportError, AttributeError):
            self.skipTest("Rust widget shorthand parsing is not available")

        separators = [
            "",
            " ",
            "\t",
            "\n",
            "\r",
            "\r\n",
            "\v",
            "\f",
            "\x1c",
            "\x1d",
            "\x1e",
            "\x1f",
            "\x85",
            "\xa0",
            "\u1680",
            *map(chr, range(0x2000, 0x200B)),
            "\u2028",
            "\u2029",
            "\u202f",
            "\u205f",
            "\u3000",
            "\u200b",
            "\ufeff",
        ]
        beginnings = ("", " ", "\u200b", "/poll", "/todo", "/Poll", "/polling", "//todo")
        tails = (
            "",
            "عنوان",
            "\n* one\n- two : desc: more",
            " /poll\r\n- ** \u2028 - *",
            "\u00a0*: empty\n- a:\tx",
        )
        with (
            patch.object(widget, "_parse_widget_shorthand", None),
            patch.object(widget, "_extract_poll_widget_text", None),
            patch.object(widget, "_extract_todo_widget_text", None),
        ):
            for beginning in beginnings:
                for separator in separators:
                    for tail in tails:
                        content = beginning + separator + tail
                        with self.subTest(content=content):
                            self.assertEqual(
                                json.dumps(parse(content)),
                                json.dumps(widget.get_widget_data(content)),
                            )

    def test_decline_and_old_symbol_use_python(self) -> None:
        class Substituted(str):
            __slots__ = ()

            @override
            def replace(self, old: str, new: str, /, count: SupportsIndex = -1) -> str:
                return "override\n- replacement"

        content = Substituted("/poll original")
        surrogate = "/todo title\n - \ud800"
        disabled = {
            content: ("poll", {"question": "override", "options": ["replacement"]}),
            surrogate: (
                "todo",
                {"task_list_title": "title", "tasks": [{"task": "\ud800", "desc": ""}]},
            ),
        }
        for value, expected in disabled.items():
            self.assertEqual(widget.get_widget_data(value), expected)
        for legacy_export in (None, Mock(return_value=None)):
            with patch.object(widget, "_parse_widget_shorthand", legacy_export):
                for value, expected in disabled.items():
                    self.assertEqual(widget.get_widget_data(value), expected)
                with self.assertRaises(TypeError):
                    widget.get_widget_data(Mock(spec=str))

    def test_enabled_subclass_command_uses_native_classification_and_python_callbacks(self) -> None:
        if os.environ.get("ZULIP_RUST_WIDGET_SHORTHAND") != "1":
            return
        classifier = widget._classify_widget_subclass
        assert classifier is not None
        calls = []

        class Substituted(str):
            __slots__ = ()

            @override
            def replace(self, old: str, new: str, /, count: SupportsIndex = -1) -> str:
                calls.append((old, new, count))
                return "callback title\n- callback item"

            @override
            def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
                raise AssertionError("a str subclass must not intercept native encoding")

        # Block the old classifier, not the old text parser used independently.
        with patch.object(
            widget,
            "re",
            Mock(wraps=widget.re, split=Mock(side_effect=AssertionError("old classifier"))),
        ):
            self.assertEqual(
                widget.get_widget_data(Substituted("/poll original")),
                ("poll", {"question": "callback title", "options": ["callback item"]}),
            )
            self.assertEqual(widget.get_widget_data(Substituted("/polling")), (None, None))
            self.assertEqual(widget.get_widget_data(Substituted("/poll\ud800")), (None, None))
        self.assertEqual(calls, [("/poll", "", 1)])

    def test_post_save_still_prefers_explicit_widget_content(self) -> None:
        send_request = Mock(spec=SendMessageRequest)
        send_request.message = Message(id=101, sender_id=202, content="/todo ignored")
        explicit = {
            "widget_type": "zform",
            "extra_data": {"type": "choices", "heading": "Explicit", "choices": []},
        }
        send_request.widget_content = explicit
        with (
            patch.object(widget, "get_widget_data", wraps=widget.get_widget_data) as parse,
            patch.object(SubMessage, "save", autospec=True) as save,
            patch.object(SubMessage, "get_raw_db_rows", return_value=[]) as fetch,
        ):
            widget.do_widget_post_save_actions(send_request)

        parse.assert_called_once_with("/todo ignored")
        saved = save.call_args.args[0]
        self.assertEqual((saved.sender_id, saved.message_id, saved.msg_type), (202, 101, "widget"))
        self.assertEqual(json.loads(saved.content), explicit)
        fetch.assert_called_once_with([101])
        self.assertEqual(send_request.submessages, [])
