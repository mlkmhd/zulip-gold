# ruff: noqa: INP001

"""Exercise the source formatter without importing the provisioned Zulip app."""

import ast
import builtins
import logging
import random
import re
import unittest
from collections.abc import Callable, Iterable, Iterator
from importlib import import_module
from pathlib import Path
from types import FunctionType, SimpleNamespace
from typing import cast
from unittest.mock import ANY, Mock

import lxml.html
from django.utils.html import escape
from markdown import Markdown
from markdown.extensions import Extension, codehilite
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.preprocessors import Preprocessor
from pygments.lexers import find_lexer_class_by_name
from pygments.util import ClassNotFound
from typing_extensions import override

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = import_module("_zulip_backend")
SOURCE = ROOT / "zerver/lib/markdown/fenced_code.py"


def load_formatter(
    backend: object,
    name: str = "format_quote",
    *,
    owner: str = "FencedBlockPreprocessor",
    extra: dict[str, object] | None = None,
) -> Callable[..., object]:
    module = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    cls = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == owner
    )
    method = next(
        node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace = {
        "_rust_markdown_fences": backend,
        "_rust_fenced_quote_text": None,
        "cast": cast,
        "override": override,
    } | (extra or {})
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), namespace)  # noqa: S102
    return cast(Callable[..., object], namespace[name])


class FencedQuoteParity(unittest.TestCase):
    def test_reference_and_live_dispatch(self) -> None:
        reference = load_formatter(None)
        old_wheel = load_formatter(SimpleNamespace())
        native = Mock(wraps=EXTENSION.format_markdown_fenced_quote)
        opted_in = load_formatter(SimpleNamespace(format_markdown_fenced_quote=native))
        fixtures = ["", "\n", "\n\n", "alpha\n", "\r\n", "a\r", "\0", "خط\n😀", "\ud800"]
        rng = random.Random(962860)
        alphabet = "a\n\r\0\t> فارسی😀\u2028\u0085"
        cases = ["".join(rng.choices(alphabet, k=rng.randrange(128))) for _ in range(20_000)]
        exact = Mock(wraps=EXTENSION.prefix_exact_fenced_quote_lines)
        default = load_formatter(
            None,
            extra={
                "_rust_fenced_quote_text": SimpleNamespace(prefix_exact_fenced_quote_lines=exact)
            },
        )
        for text in fixtures + cases:
            result = reference(None, text)
            self.assertEqual(default(None, text), result)
            self.assertEqual(old_wheel(None, text), result)
            self.assertEqual(opted_in(None, text), result)
        self.assertEqual(native.call_count, len(fixtures) + len(cases))
        self.assertEqual(exact.call_count, len(fixtures) + len(cases))
        self.assertIsNone(EXTENSION.format_markdown_fenced_quote("\ud800"))

    def test_exact_type_and_output_budget_delegate(self) -> None:
        class CustomSplit(str):
            __slots__ = ()

            def split(self, *args: object, **kwargs: object) -> list[str]:
                return ["overridden"]

        native = Mock(wraps=EXTENSION.format_markdown_fenced_quote)
        backend = SimpleNamespace(format_markdown_fenced_quote=native)
        reference = load_formatter(None)
        opted_in = load_formatter(backend)
        text = CustomSplit("ignored")
        self.assertIsNone(EXTENSION.format_markdown_fenced_quote(text))
        self.assertEqual(opted_in(None, text), "> overridden")
        native.assert_not_called()

        limit = 8 * 1024 * 1024
        inside = "a" * (limit - 2)
        outside = "\n" * (limit // 3 + 1)
        self.assertEqual(EXTENSION.format_markdown_fenced_quote(inside), "> " + inside)
        self.assertTrue(EXTENSION.format_markdown_fenced_quote(outside) is None)
        self.assertTrue(opted_in(None, outside) == reference(None, outside))
        native.assert_called_once_with(outside)


class FencedSpoilerParity(unittest.TestCase):
    def test_callback_order_and_results(self) -> None:
        for header, text, failure in [
            (None, "normal", None),
            ("", "", None),
            ("header\ud800", "فارسی\0", None),
            ("header", "text", 1),
            ("header", "text", 2),
            ("header", "text", 3),
            ("header", "text", "invalid-fragment"),
        ]:
            with self.subTest(header=header, text=text, failure=failure):
                baseline = self.exercise(None, header, text, failure)
                self.assertEqual(self.exercise(SimpleNamespace(), header, text, failure), baseline)
                self.assertEqual(self.exercise(EXTENSION, header, text, failure), baseline)

    def exercise(
        self, backend: object, header: str | None, text: str, failure: int | str | None
    ) -> tuple[object, list[str]]:
        formatter = load_formatter(backend, "format_spoiler")
        seen: list[str] = []
        processor = SimpleNamespace()
        original = RuntimeError("placeholder failed")

        def make_placeholder(number: int) -> Callable[[str], object]:
            def placeholder(value: str) -> object:
                seen.append(value)
                if failure == number:
                    raise original
                # The host may replace the method during a callback.
                processor.placeholder = make_placeholder(number + 1)
                if failure == "invalid-fragment" and number == 2:
                    return 17
                return f"{{{number}}}"

            return placeholder

        processor.placeholder = make_placeholder(1)
        try:
            result: object = formatter(processor, header, text)
        except RuntimeError as error:
            self.assertIs(error, original)
            result = (type(error), error.args)
        except TypeError as error:
            self.assertEqual(seen, [ANY, ANY, ANY])
            result = (type(error), error.args)
        return result, seen


class FencedCompletionParity(unittest.TestCase):
    def test_completion_dispatch_and_failure_order(self) -> None:
        for stage in [
            "empty",
            "code",
            "nested",
            "surrogate",
            "bad-join",
            "format",
            "placeholder",
            "split",
            "first-append",
            "extend",
            "last-append",
            "pop",
        ]:
            with self.subTest(stage=stage):
                reference = self.exercise(None, stage)
                self.assertEqual(self.exercise(SimpleNamespace(), stage), reference)
                finish = Mock(wraps=EXTENSION.finish_fenced_block)
                native = SimpleNamespace(finish_fenced_block=finish)
                self.assertEqual(self.exercise(native, stage), reference)
                finish.assert_called_once()

    def exercise(self, backend: object, stage: str) -> tuple[list[object], object]:
        done = load_formatter(backend, "done", owner="ZulipBaseHandler")
        seen: list[object] = []
        error = RuntimeError(stage)
        original = b"original \x00 line".decode()
        handler = SimpleNamespace(
            lines=[] if stage == "empty" else [17] if stage == "bad-join" else [original],
            process_contents=stage in {"nested", "surrogate"},
        )

        class Rendered(str):
            __slots__ = ()

            def split(self, sep: str, maxsplit: int = -1) -> list[str]:
                seen.append(("split", sep, maxsplit))
                if stage == "split":
                    raise error
                return super().split(sep, maxsplit)

        def format_text(text: str) -> str:
            seen.append(("format", text, text is original))
            if stage == "format":
                raise error
            return Rendered("فارسی\ud800\n" if stage == "surrogate" else "formatted\n")

        def placeholder(text: str) -> str:
            seen.append(("placeholder", text))
            if stage == "placeholder":
                raise error
            return Rendered("stash\n")

        def pop() -> None:
            seen.append("pop")
            if stage == "pop":
                raise error

        class Output:
            def __init__(self, number: int) -> None:
                self.number = number

            def append(self, item: str) -> None:
                seen.append(("append", self.number, item))
                if stage == ("first-append" if self.number == 1 else "last-append"):
                    raise error
                handler.output = Output(self.number + 1)

            def extend(self, items: list[str]) -> None:
                seen.append(("extend", self.number, items))
                if stage == "extend":
                    raise error
                handler.output = Output(self.number + 1)

        handler.format_text = format_text
        handler.output = Output(1)
        handler.processor = SimpleNamespace(placeholder=placeholder, pop=pop)
        try:
            return seen, done(handler)
        except RuntimeError as caught:
            self.assertIs(caught, error)
            return seen, (type(caught), caught.args)
        except TypeError as caught:
            self.assertEqual(stage, "bad-join")
            return seen, (type(caught), caught.args)


class FencedLineParity(unittest.TestCase):
    def test_plain_lines_and_closing_whitespace(self) -> None:
        lines = ["  a\t", "தமிழ்\x00\r\n", "foo\u001c", "\ud800\u2003", "```\t\u0085"]
        for backend in [None, SimpleNamespace(), EXTENSION]:
            method = load_formatter(backend, "handle_line", owner="ZulipBaseHandler")
            closed = Mock()
            handler = SimpleNamespace(fence="```", lines=[], done=closed)
            for line in lines:
                method(handler, line)
            self.assertEqual(handler.lines, ["  a", "தமிழ்\x00", "foo", "\ud800"])
            closed.assert_called_once_with()

    def test_rich_comparison_and_callback_order(self) -> None:
        for stage in [
            "closed",
            "content",
            "rstrip-first",
            "compare",
            "truth",
            "done",
            "resolve-append",
            "rstrip-second",
            "append",
        ]:
            with self.subTest(stage=stage):
                expected = self.exercise(None, stage)
                self.assertEqual(self.exercise(SimpleNamespace(), stage), expected)
                native = Mock(wraps=EXTENSION.handle_fenced_block_line)
                self.assertEqual(
                    self.exercise(SimpleNamespace(handle_fenced_block_line=native), stage), expected
                )
                native.assert_called_once()

    def exercise(self, backend: object, stage: str) -> tuple[list[str], object]:
        method = load_formatter(backend, "handle_line", owner="ZulipBaseHandler")
        seen: list[str] = []
        error = ValueError(stage)

        def record(name: str) -> None:
            seen.append(name)
            if stage == name:
                raise error

        class Truth:
            def __bool__(self) -> bool:
                record("truth")
                return stage in {"closed", "done"}

        class Fence:  # noqa: PLW1641 - test an effectful comparison, not hashing.
            def __eq__(self, other: object) -> object:
                record("compare")
                return Truth()

        fence = Fence()
        handler = SimpleNamespace(fence=fence, done=lambda: record("done"))

        def append(item: object) -> None:
            self.assertTrue(item is fence)
            record("append")

        class Buffer:
            @property
            def append(self) -> Callable[[object], None]:
                record("resolve-append")
                return append

        class Line:
            calls = 0

            def rstrip(self) -> object:
                self.calls += 1
                record("rstrip-first" if self.calls == 1 else "rstrip-second")
                if self.calls > 1:
                    handler.lines = None
                return fence

        handler.lines = Buffer()
        try:
            return seen, method(handler, Line())
        except ValueError as caught:
            self.assertIs(caught, error)
            return seen, (type(caught), caught.args)


class NestedFencedLineParity(unittest.TestCase):
    def test_quote_and_spoiler_dispatch(self) -> None:
        stages = ["closed", "open", "done", "processor", "lines", "language", "nested-check"]
        for owner in ["QuoteHandler", "SpoilerHandler"]:
            for stage in stages:
                with self.subTest(owner=owner, stage=stage):
                    reference = self.exercise(None, owner, stage)
                    self.assertEqual(self.exercise(SimpleNamespace(), owner, stage), reference)
                    native = Mock(wraps=EXTENSION.handle_nested_fenced_block_line)
                    backend = SimpleNamespace(handle_nested_fenced_block_line=native)
                    self.assertEqual(self.exercise(backend, owner, stage), reference)
                    native.assert_called_once()

    def exercise(self, backend: object, owner: str, stage: str) -> tuple[list[object], object]:
        seen: list[object] = []
        error = RuntimeError(stage)
        processor: object = object()
        output: list[object] = []
        language: object = object()
        line_text = "```\t" if stage in {"closed", "done"} else "~~~~\u2003"
        line = line_text.encode().decode()

        def record(event: str) -> None:
            seen.append(event)
            if stage == event:
                raise error

        def check(
            actual_processor: object,
            actual_output: object,
            actual_line: object,
            /,
            **kwargs: object,
        ) -> None:
            self.assertIs(actual_processor, processor)
            self.assertIs(actual_output, output)
            self.assertIs(actual_line, line)
            if owner == "QuoteHandler":
                self.assertEqual(set(kwargs), {"default_language"})
                self.assertIs(kwargs["default_language"], language)
            else:
                self.assertEqual(kwargs, {})
            record("nested-check")

        method = load_formatter(
            backend, "handle_line", owner=owner, extra={"check_for_new_fence": check}
        )

        class Handler:
            fence = "```"

            @property
            def processor(self) -> object:
                record("processor")
                return processor

            @property
            def lines(self) -> list[object]:
                record("lines")
                return output

            @property
            def default_language(self) -> object:
                assert owner == "QuoteHandler"
                record("language")
                return language

            def done(self) -> None:
                record("done")

        try:
            return seen, method(Handler(), line)
        except RuntimeError as caught:
            self.assertIs(caught, error)
            return seen, (type(caught), caught.args)


def load_fence_stack(
    backend: object, *, extra: dict[str, object] | None = None
) -> dict[str, object]:
    source = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    future = ast.parse("from __future__ import annotations").body
    names = {
        "FENCE_RE",
        "CODE_WRAP",
        "LANG_TAG",
        "_original_parse_fence_line",
        "_original_curl_re_search",
        "CODE_VALIDATORS",
        "parse_fence_line",
        "check_for_new_fence",
        "generic_handler",
        "validate_curl_content",
        "FencedCodeExtension",
        "CodeHilite",
        "ZulipBaseHandler",
        "OuterHandler",
        "QuoteHandler",
        "SpoilerHandler",
        "CodeHandler",
        "TexHandler",
        "_BLOCK_PLAN_LIST_HANDLERS",
        "FencedBlockPreprocessor",
    }
    body = []
    for node in source.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            name = node.name
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        else:
            continue
        if name in names:
            body.append(node)

    models_source = ast.parse(
        (ROOT / "zerver/models/realm_playgrounds.py").read_text(encoding="utf-8")
    )
    language = next(
        ast.literal_eval(item.value)
        for item in models_source.body
        if isinstance(item, ast.Assign)
        and isinstance(item.targets[0], ast.Name)
        and item.targets[0].id == "PLAYGROUND_LANGUAGE_REGEX"
    )
    namespace = {
        "_rust_markdown_fences": backend,
        "_rust_fenced_curl": None,
        "_rust_markdown_blocks": None,
        "_rust_code_escape": None,
        "_rust_fenced_quote_text": None,
        "cast": cast,
        "override": override,
        "re": re,
        "Preprocessor": Preprocessor,
        "PLAYGROUND_LANGUAGE_REGEX": language,
        "MarkdownRenderingError": ValueError,
        "Extension": Extension,
        "codehilite": codehilite,
    } | (extra or {})
    exec(compile(ast.Module(body=future + body, type_ignores=[]), str(SOURCE), "exec"), namespace)  # noqa: S102
    return namespace


class NestedFencedStackParity(unittest.TestCase):
    def test_real_nested_stack_matches_disabled_and_old_wheel(self) -> None:
        cases = [
            ["plain", "~~~quote", "before", "```spoiler", "secret", "```", "after", "~~~"],
            ["~~~quote", "```", "nested default code", "```", "~~~", "after"],
            ["~~~spoiler Summary", "```", "nested no-language code", "```", "~~~"],
            ["~~~quote", "```math", "α", "```", "never closed"],
            ["~~~quote", "```python", "str = 'فارسی'", "```", "~~~", "line\r"],
            ["~~~curl", "curl invalid", "~~~"],
            ["~~~curl", 'curl -sSX "POST" https://example.test', "~~~"],
        ]
        old_wheel = SimpleNamespace(parse_markdown_fence=EXTENSION.parse_markdown_fence)
        for lines in cases:
            with self.subTest(lines=lines):
                expected = self.run_stack(None, lines)
                self.assertEqual(self.run_stack(old_wheel, lines), expected)
                self.assertEqual(self.run_stack(EXTENSION, lines), expected)

    def run_stack(self, backend: object, lines: list[str]) -> tuple[object, ...]:
        namespace = load_fence_stack(backend)
        base = cast(type, namespace["FencedBlockPreprocessor"])

        class Processor(base):
            def format_code(self, lang: str | None, text: str) -> str:
                return f"code({lang}:{text})"

            def format_tex(self, text: str) -> str:
                return f"math({text})"

        md = Markdown()
        processor = Processor(md, run_content_validators=True)
        output: list[str] = []
        error: object = None
        try:
            processor.handle_lines(lines, output, "python")
            processor._finish_fenced_input(output)
        except ValueError as caught:
            error = (type(caught), caught.args)
        return output, md.htmlStash.rawHtmlBlocks, md.htmlStash.html_counter, error

    def test_override_delegates_before_parsing(self) -> None:
        native = Mock(wraps=EXTENSION.dispatch_new_fenced_block)
        backend = SimpleNamespace(
            parse_markdown_fence=EXTENSION.parse_markdown_fence, dispatch_new_fenced_block=native
        )
        scope = load_fence_stack(backend)
        output: list[str] = []
        call = cast(Callable[..., object], scope["check_for_new_fence"])
        call(SimpleNamespace(), output, "before")
        native.assert_called_once()
        parser = Mock(return_value=None)
        scope["parse_fence_line"] = parser
        call(SimpleNamespace(), output, "after")
        parser.assert_called_once_with("after")
        native.assert_called_once()
        self.assertEqual(output, ["before", "after"])


class FencedHandlerFactoryParity(unittest.TestCase):
    def test_live_factories_and_unicode_normalization(self) -> None:
        cases = [None, "", "QuOtE", "QUOTED", "MaTh", "SPoiLer", "PYTHON", "İ", "Σ", "\ud800"]
        native = Mock(wraps=EXTENSION.create_fenced_block_handler)
        backend = SimpleNamespace(create_fenced_block_handler=native)
        for lang in cases:
            with self.subTest(lang=lang):
                disabled = self.exercise(None, lang)
                self.assertEqual(self.exercise(SimpleNamespace(), lang), disabled)
                self.assertEqual(self.exercise(backend, lang), disabled)
        self.assertEqual(native.call_count, 2 * len(cases))
        native.reset_mock()

        class Language(str):
            __slots__ = ()

            def lower(self) -> str:
                return "math"

        self.assertEqual(self.exercise(backend, Language("quote"))[0], "TexHandler")
        native.assert_not_called()

    def exercise(self, backend: object, lang: str | None) -> tuple[str, tuple[object, ...]]:
        namespace = load_fence_stack(backend)
        processor = object()
        output: list[str] = []
        fence = b"~~~".decode()
        header = b"header".decode()
        default_language = b"default".decode()
        validators = object()
        select = cast(Callable[..., object], namespace["generic_handler"])

        def factory(name: str) -> Callable[..., object]:
            def create(*args: object, **kwargs: object) -> object:
                self.assertEqual(kwargs, {})
                self.assertIs(args[0], processor)
                self.assertIs(args[1], output)
                self.assertIs(args[2], fence)
                if name == "QuoteHandler":
                    self.assertIs(args[3], default_language)
                if name == "SpoilerHandler":
                    self.assertIs(args[3], header)
                if name == "CodeHandler":
                    self.assertIs(args[4], validators)
                return (name, args[3:4])

            return create

        for name in ["QuoteHandler", "TexHandler", "SpoilerHandler", "CodeHandler"]:
            namespace[name] = factory(name)
        result = select(processor, output, fence, lang, header, validators, default_language)
        error = RuntimeError("live factory failed")
        selected = cast(tuple[str, tuple[object, ...]], result)[0]
        failing = Mock(side_effect=error)
        namespace[selected] = failing
        with self.assertRaises(RuntimeError) as caught:
            select(processor, output, fence, lang, header, validators, default_language)
        self.assertIs(caught.exception, error)
        failing.assert_called_once()
        return cast(tuple[str, tuple[object, ...]], result)


class FencedInputCompletionParity(unittest.TestCase):
    def test_stack_and_padding_keep_evaluation_order(self) -> None:
        cases = [
            "empty",
            "short",
            "already-padded",
            "append",
            "done-error",
            "len-error",
            "index-error",
            "compare-error",
            "truth-error",
            "append-error",
        ]
        native = Mock(wraps=EXTENSION.finish_fenced_input)
        backend = SimpleNamespace(finish_fenced_input=native)
        for stage in cases:
            with self.subTest(stage=stage):
                baseline = self.exercise(None, stage)
                self.assertEqual(self.exercise(SimpleNamespace(), stage), baseline)
                self.assertEqual(self.exercise(backend, stage), baseline)
        self.assertEqual(native.call_count, len(cases))

    def exercise(self, backend: object, stage: str) -> tuple[list[object], object]:
        seen: list[object] = []
        error = RuntimeError(stage)

        def record(name: str) -> None:
            seen.append(name)
            if stage == name:
                raise error

        class Truth:
            def __bool__(self) -> bool:
                record("truth-error")
                return stage != "already-padded"

        class PaddingProbe:
            def __ne__(self, other: object) -> object:
                seen.append(("compared-to", other))
                record("compare-error")
                return Truth()

        class Output:
            def __len__(self) -> int:
                record("len-error")
                return 2 if stage == "short" else 3

            def __getitem__(self, index: int) -> object:
                seen.append(("index", index))
                record("index-error")
                return PaddingProbe()

            def append(self, line: str) -> None:
                seen.append(("padding", line))
                record("append-error")

        processor = SimpleNamespace(handlers=[])

        def later() -> None:
            record("later-handler")
            processor.handlers.clear()

        def first() -> None:
            record("done-error")
            processor.handlers = [SimpleNamespace(done=later)]

        if stage != "empty":
            processor.handlers = [SimpleNamespace(done=first)]
        finish = load_formatter(backend, "_finish_fenced_input")
        try:
            return seen, finish(processor, Output())
        except RuntimeError as caught:
            self.assertIs(caught, error)
            return seen, (type(caught), caught.args)


class FencedInputDispatchParity(unittest.TestCase):
    def test_live_iterator_and_handler_stack(self) -> None:
        native = Mock(wraps=EXTENSION.dispatch_fenced_input_lines)
        backend = SimpleNamespace(dispatch_fenced_input_lines=native)
        stages = ["empty", "success", "iter", "next", "lookup", "callback", "planner-miss", "plan"]
        for stage in stages:
            with self.subTest(stage=stage):
                reference = self.exercise(None, stage)
                self.assertEqual(self.exercise(SimpleNamespace(), stage), reference)
                self.assertEqual(self.exercise(backend, stage), reference)
        self.assertEqual(native.call_count, len(stages) - 1)

    def exercise(self, backend: object, stage: str) -> tuple[object, ...]:
        seen: list[object] = []
        error = RuntimeError(stage)
        first_line, second_line = object(), object()

        def record(name: str) -> None:
            seen.append(name)
            if stage == name:
                raise error

        class Lines:
            def __iter__(self) -> "Lines":
                record("iter")
                self.offset = 0
                return self

            def __next__(self) -> object:
                self.offset += 1
                if self.offset == 1 and stage != "empty":
                    return first_line
                if self.offset == 2:
                    record("next")
                    return second_line
                raise StopIteration

        class Stack(list):
            def __getitem__(self, index: int) -> object:
                seen.append(index)
                record("lookup")
                return super().__getitem__(index)

        scope = load_fence_stack(backend)
        outer = cast(type, scope["OuterHandler"])

        class Outer(outer):
            def handle_line(self, line: object) -> None:
                self_test.assertIs(line, first_line)
                record("callback")
                self.processor.handlers = Stack([SimpleNamespace(handle_line=second)])

        def second(line: object) -> None:
            self.assertIs(line, second_line)
            record("replacement")

        def plan(lines: list[object], language: str | None) -> object:
            self.assertEqual(lines, [first_line, second_line])
            self.assertEqual(language, "python")
            record("planner")
            return [("line", "planned", None, None, None)] if stage == "plan" else None

        self_test = self
        scope["OuterHandler"] = Outer
        if stage in {"planner-miss", "plan"}:
            scope["_rust_markdown_blocks"] = SimpleNamespace(plan_markdown_blocks=plan)

        base = cast(type, scope["FencedBlockPreprocessor"])

        class Processor(base):
            def push(self, handler: object) -> None:
                self.handlers = Stack([handler])

        output: list[str] = []
        processor = Processor(Markdown(), run_content_validators=False)
        result = None
        try:
            processor.handle_lines(Lines(), output, "python")
        except RuntimeError as caught:
            self.assertIs(caught, error)
            result = caught.args
        return seen, output, result

    def test_previous_line_stays_live_during_iterator_advance(self) -> None:
        def exercise(dispatch: Callable[..., object]) -> list[object]:
            seen: list[object] = []

            class Line:
                def __del__(self) -> None:
                    seen.append("drop-line")

            def lines() -> Iterable[object]:
                try:
                    yield Line()
                    seen.append("resume")
                    yield Line()
                    seen.append("exhaust")
                finally:
                    seen.append("finish")

            handler = SimpleNamespace(handle_line=lambda line: seen.append("handle"))
            dispatch(SimpleNamespace(handlers=[handler]), lines())
            return seen

        def reference(processor: SimpleNamespace, lines: Iterable[object]) -> None:
            for line in lines:
                processor.handlers[-1].handle_line(line)

        self.assertEqual(exercise(EXTENSION.dispatch_fenced_input_lines), exercise(reference))


class FencedCurlValidationParity(unittest.TestCase):
    def test_native_reports_first_error_and_delegation(self) -> None:
        find = EXTENSION.find_invalid_fenced_curl_line
        self.assertEqual(find(["text", "curl -X GET", "curl -X PUT", "curl invalid"]), 2)
        self.assertEqual(find(["text", "curl -sSX PATCH"]), -1)
        self.assertEqual(find(["text", "curl -sX PATCH"]), 1)
        self.assertEqual(find(["curl invalid", "\ud800"]), 0)
        self.assertIsNone(find(["\ud800", "curl invalid"]))
        self.assertIsNone(find(["curl -sSX PATCH", object()]))
        self.assertIsNone(find(("curl -sX PATCH",)))

    def test_original_validator_matches_native(self) -> None:
        native = Mock(wraps=EXTENSION.find_invalid_fenced_curl_line)
        validator = self.load(SimpleNamespace(find_invalid_fenced_curl_line=native))
        prepared = load_fence_stack(
            None, extra={"_rust_fenced_curl": SimpleNamespace(find_invalid_fenced_curl_line=native)}
        )
        default_validator = cast(Callable[..., object], prepared["validate_curl_content"])
        original = self.load(None)
        old = self.load(SimpleNamespace())
        cases = [
            [],
            ["unrelated", " curl invalid", "CURL invalid", "\t", "خط", "😀"],
            ["curl -X GET", "curl -sSX DELETE", 'curl -sSX "PATCH"', "curl -X POST"],
            ["curl -X POSTfix", 'curling curl -X "GET', "curl -X GET\nand more"],
            ["curl -X\tGET", "curl -X GET"],
            ["curl -X PUT", "curl first error must win"],
            ["curl -SsX GET"],
            ["curl -sX GET"],
            ["curl -SX GET"],
            ["curl prefix\ncurl -X POST"],
            ["curl -X post\x1c", "\ud800"],
            ["\ud800", "curl \ud800"],
        ]
        rng = random.Random(904822)
        words = [
            "curl",
            "curling",
            "-X",
            "-sX",
            "-SX",
            "-sSX",
            "-SsX",
            "GET",
            "POST",
            "PUT",
            '"',
            " ",
            "\t",
        ]
        cases += [
            ["".join(rng.choices(words, k=rng.randrange(9))) for _ in range(rng.randrange(5))]
            for _ in range(2_000)
        ]
        for lines in cases:
            expected = self.run_validator(original, lines)
            self.assertEqual(self.run_validator(old, lines), expected)
            self.assertEqual(self.run_validator(validator, lines), expected)
            self.assertEqual(self.run_validator(default_validator, lines), expected)
        self.assertEqual(native.call_count, 2 * len(cases))

    def test_custom_types_and_search_keep_python_behavior(self) -> None:
        native = Mock(wraps=EXTENSION.find_invalid_fenced_curl_line)
        validator = self.load(SimpleNamespace(find_invalid_fenced_curl_line=native))

        class CustomLine(str):
            __slots__ = ()

            def startswith(self, prefix: str) -> bool:
                self_test.assertEqual(prefix, "curl")
                return False

        class CustomLines(list):
            def __iter__(self) -> Iterator[str]:
                yield "curl custom list"

        self_test = self
        self.assertIsNone(self.run_validator(validator, [CustomLine("curl custom line")]))
        self.assertEqual(
            self.run_validator(validator, CustomLines()),
            self.run_validator(self.load(None), CustomLines()),
        )
        self.assertEqual(native.call_count, 2)
        namespace = load_fence_stack(SimpleNamespace(find_invalid_fenced_curl_line=native))
        replacement = Mock(return_value=True)
        namespace["re"] = SimpleNamespace(search=replacement)
        cast(Callable[..., object], namespace["validate_curl_content"])(["curl without method"])
        replacement.assert_called_once()
        self.assertEqual(native.call_count, 2)

    @staticmethod
    def load(backend: object) -> Callable[..., object]:
        scope = load_fence_stack(backend)
        return cast(Callable[..., object], scope["validate_curl_content"])

    @staticmethod
    def run_validator(validator: Callable[..., object], lines: object) -> object:
        try:
            return validator(lines)
        except ValueError as error:
            return type(error), error.args


class FencedFixtureMarkdown(Markdown):
    def __init__(self, default_language: str | None, extensions: list[Extension]) -> None:
        self.zulip_realm = SimpleNamespace(default_code_block_language=default_language)
        super().__init__(extensions=extensions)
        self.preprocessors.deregister("html_block")


def make_fenced_render_fixture(
    backend: object,
    *,
    default_language: str | None = None,
    highlight: bool = False,
    python_parser: bool = False,
    block_planner: object = None,
) -> FencedFixtureMarkdown:
    def fixture_import(name: str, *args: object) -> object:
        # The source's run method imports only this renderer type; the real
        # Zulip package would require a provisioned Django environment.
        if name == "zerver.lib.markdown":
            return SimpleNamespace(ZulipMarkdown=FencedFixtureMarkdown)
        return builtins.__import__(name, *args)

    def render_tex(text: str, *, is_inline: bool) -> str | None:
        assert not is_inline
        return None if text == "invalid" else f'<span class="test-math">{escape(text)}</span>'

    scope = load_fence_stack(
        backend,
        extra={
            "__builtins__": vars(builtins) | {"__import__": fixture_import},
            "PREPROCESSOR_PRIORITIES": {"fenced_code_block": 25},
            "Markdown": Markdown,
            "CodeHiliteExtension": CodeHiliteExtension,
            "ClassNotFound": ClassNotFound,
            "find_lexer_class_by_name": find_lexer_class_by_name,
            "lxml": lxml,
            "logging": logging,
            "render_tex": render_tex,
            "escape": escape,
            "_rust_code_header": None,
            "_rust_code_highlight": None,
            "_rust_tex_handlers": None,
            "_rust_markdown_blocks": block_planner,
            "parse_hl_lines": codehilite.parse_hl_lines,
        },
    )
    if python_parser:
        # Only the parser gets the Python backend; its callers and handler
        # factory keep the fixture's Rust imports and original-parser guard.
        parser = cast(FunctionType, scope["parse_fence_line"])
        reference = FunctionType(parser.__code__, scope | {"_rust_markdown_fences": None})
        scope.update(parse_fence_line=reference, _original_parse_fence_line=reference)
    extension = cast(type[Extension], scope["FencedCodeExtension"])
    extensions = [extension({"run_content_validators": True})]
    if highlight:
        extensions.append(CodeHiliteExtension(guess_lang=False))
    return FencedFixtureMarkdown(default_language, extensions)


TICKS = chr(96) * 3
FENCED_RENDER_SAMPLES = {
    "plain": "Hello **Markdown** with فارسی and a link [docs](https://example.test).",
    "code": 'Before\n\n~~~python\nprint("فارسی <&>")\n~~~\n\nAfter',
    "nested": (
        "~~~quote\nQuoted **text**\n\n"
        f"{TICKS}spoiler Title *here*\nA **secret**.\n\n"
        "~~~~javascript\nconst x = '<tag>';\n~~~~\n"
        "~~~math\nx < 2\n\ninvalid\n~~~\n"
        f"{TICKS}\n~~~"
    ),
    "incomplete": f"Top\n\n~~~~quote\n{TICKS}python\nimport sys",
    "default-language": "~~~\nconst x = 1;\n~~~",
    "curl-valid": '~~~curl\ncurl -sSX "POST" https://example.test\n~~~',
}


class FencedMarkdownRenderingParity(unittest.TestCase):
    def test_native_plan_replay_matches_legacy_plan(self) -> None:
        replay = Mock(wraps=EXTENSION.replay_markdown_block_plan)
        current = make_fenced_render_fixture(
            None, block_planner=SimpleNamespace(replay_markdown_block_plan=replay)
        )
        old = make_fenced_render_fixture(
            None, block_planner=SimpleNamespace(plan_markdown_blocks=EXTENSION.plan_markdown_blocks)
        )
        sample = FENCED_RENDER_SAMPLES["nested"]
        self.assertEqual(render_fenced_fixture(current, sample), render_fenced_fixture(old, sample))
        replay.assert_called_once()

    def test_native_plan_rereads_stack_for_custom_output(self) -> None:
        def replay(blocks: object) -> tuple[list[str], list[str]]:
            scope = load_fence_stack(None, extra={"_rust_markdown_blocks": blocks})
            processor = cast(type, scope["FencedBlockPreprocessor"])(Markdown())
            factory = cast(Callable[..., object], scope["generic_handler"])
            captured: list[str] = []

            class CustomOutput(list):
                def append(self, value: str) -> None:
                    captured.append(value)
                    processor.pop()

            def custom_factory(*args: object) -> object:
                handler = factory(*args)
                setattr(handler, "lines", CustomOutput())  # noqa: B010
                return handler

            scope["generic_handler"] = custom_factory
            output: list[str] = []
            processor.handle_lines(["~~~python", "one", "two", "three"], output, None)
            return output, captured

        old = SimpleNamespace(plan_markdown_blocks=EXTENSION.plan_markdown_blocks)
        self.assertEqual(replay(EXTENSION), replay(old))
        self.assertEqual(replay(EXTENSION), (["two", "three"], ["one"]))

    def test_decline_is_before_effects_and_callback_failure_is_not_retried(self) -> None:
        replay = EXTENSION.replay_markdown_block_plan

        class CustomLine(str):
            __slots__ = ()

        output: list[str] = []
        processor = Mock()
        for lines in (["one", "\ud800"], ["one", CustomLine("two")], ["one", object()]):
            self.assertFalse(replay(processor, output, lines, None, {}, ()))
            self.assertEqual(output, [])
            self.assertEqual(processor.mock_calls, [])

        legacy = Mock(side_effect=AssertionError("legacy plan must not retry"))
        backend = SimpleNamespace(replay_markdown_block_plan=replay, plan_markdown_blocks=legacy)
        scope = load_fence_stack(None, extra={"_rust_markdown_blocks": backend})
        processor = cast(type, scope["FencedBlockPreprocessor"])(Markdown())
        error = ValueError("format callback")
        processor.format_code = Mock(side_effect=error)
        with self.assertRaises(ValueError) as caught:
            processor.handle_lines(["~~~python", "one", "two", "~~~"], output, None)
        self.assertIs(caught.exception, error)
        processor.format_code.assert_called_once()
        legacy.assert_not_called()

    def test_fixture_rendering_matches_python_and_old_wheel(self) -> None:
        old_wheel = SimpleNamespace(parse_markdown_fence=EXTENSION.parse_markdown_fence)
        rng = random.Random(390544)
        samples = list(FENCED_RENDER_SAMPLES.values())
        samples += ["\r\n\r\n".join(rng.choices(samples, k=rng.randrange(1, 5))) for _ in range(80)]
        samples += ["~~~curl\ncurl -sX GET\n~~~", "~~~curl\ncurl -X PUT\n~~~"]
        for highlight in (False, True):
            for language in (None, "javascript"):
                reference, legacy, native, planner, combined = [
                    make_fenced_render_fixture(
                        backend,
                        default_language=language,
                        highlight=highlight,
                        block_planner=blocks,
                    )
                    for backend, blocks in (
                        (None, None),
                        (old_wheel, None),
                        (EXTENSION, None),
                        (None, EXTENSION),
                        (EXTENSION, EXTENSION),
                    )
                ]
                for sample in samples:
                    with self.subTest(highlight=highlight, language=language, sample=sample):
                        expected = render_fenced_fixture(reference, sample)
                        self.assertEqual(render_fenced_fixture(legacy, sample), expected)
                        self.assertEqual(render_fenced_fixture(native, sample), expected)
                        self.assertEqual(render_fenced_fixture(planner, sample), expected)
                        self.assertEqual(render_fenced_fixture(combined, sample), expected)

    def test_fixture_visits_real_formatter_and_stash(self) -> None:
        md = make_fenced_render_fixture(EXTENSION, default_language="javascript", highlight=True)
        html = md.convert(FENCED_RENDER_SAMPLES["code"])
        self.assertIn('data-code-language="Python"', html)
        self.assertIn('class="codehilite"', html)
        html = md.reset().convert(FENCED_RENDER_SAMPLES["nested"])
        self.assertIn("<blockquote>", html)
        self.assertIn('class="spoiler-block"', html)
        self.assertIn('class="test-math"', html)
        self.assertIn('class="tex-error"', html)
        html = md.reset().convert(FENCED_RENDER_SAMPLES["default-language"])
        self.assertIn('data-code-language="JavaScript"', html)

    def test_changed_native_quote_is_visible_at_html_boundary(self) -> None:
        mutant = SimpleNamespace(**vars(EXTENSION))
        mutate = Mock(return_value="changed quote")
        mutant.format_markdown_fenced_quote = mutate
        python = make_fenced_render_fixture(None)
        native = make_fenced_render_fixture(mutant)
        sample = "~~~quote\nOriginal quote\n~~~"
        self.assertNotEqual(python.convert(sample), native.convert(sample))
        mutate.assert_called_once()

    def test_python_parser_override_keeps_native_dispatch_and_factory(self) -> None:
        backend = SimpleNamespace(**vars(EXTENSION))
        parser = Mock(side_effect=AssertionError("the native parser must not be called"))
        dispatch = Mock(wraps=EXTENSION.dispatch_new_fenced_block)
        factory = Mock(wraps=EXTENSION.create_fenced_block_handler)
        backend.parse_markdown_fence = parser
        backend.dispatch_new_fenced_block = dispatch
        backend.create_fenced_block_handler = factory
        reference = make_fenced_render_fixture(None)
        native = make_fenced_render_fixture(backend, python_parser=True)
        sample = FENCED_RENDER_SAMPLES["code"]
        self.assertEqual(
            render_fenced_fixture(reference, sample), render_fenced_fixture(native, sample)
        )
        parser.assert_not_called()
        self.assertGreater(dispatch.call_count, 0)
        factory.assert_called_once()

    def test_batch_planner_accepts_exact_ascii_and_unicode_defaults(self) -> None:
        for language in ("javascript", "İΣ", "ΟΣ", "Kotlin", "فارسی"):
            with self.subTest(language=language):
                planner = Mock(wraps=EXTENSION.plan_markdown_blocks)
                rendered = make_fenced_render_fixture(
                    None,
                    default_language=language,
                    block_planner=SimpleNamespace(plan_markdown_blocks=planner),
                )
                reference = make_fenced_render_fixture(None, default_language=language)
                sample = f"~~~~quote\nNested language\n\n{TICKS}\nbody\n{TICKS}\n~~~~"
                self.assertEqual(
                    render_fenced_fixture(rendered, sample),
                    render_fenced_fixture(reference, sample),
                )
                planner.assert_called_once()
                args = planner.call_args.args
                self.assertIs(type(args[0]), list)
                self.assertEqual(args[1], language)
                steps = EXTENSION.plan_markdown_blocks(*args)
                self.assertIsNotNone(steps)
                self.assertIn(("open", TICKS, language.lower(), None, language), steps)
                unclosed = f"~~~~quote\n{TICKS}\nbody"
                self.assertEqual(
                    EXTENSION.close_unclosed_markdown_fences(unclosed, language),
                    f"{unclosed}\n{TICKS}\n~~~~",
                )

    def test_batch_planner_keeps_surrogate_and_subclass_fallbacks(self) -> None:
        class CustomLanguage(str):
            __slots__ = ()

            def lower(self) -> str:
                return "quote"

        content = "~~~\nbody"
        for language in ("\ud800", CustomLanguage("ASCII"), CustomLanguage("İΣ")):
            with self.subTest(language=language):
                self.assertIsNone(EXTENSION.plan_markdown_blocks(content.split("\n"), language))
                self.assertIsNone(EXTENSION.close_unclosed_markdown_fences(content, language))
        language = CustomLanguage("ASCII")
        python = make_fenced_render_fixture(None, default_language=language)
        opt_in = make_fenced_render_fixture(
            None, default_language=language, block_planner=EXTENSION
        )
        self.assertEqual(
            render_fenced_fixture(python, content), render_fenced_fixture(opt_in, content)
        )
        self.assertIn("<blockquote>", opt_in.reset().convert(content))


def render_fenced_fixture(md: FencedFixtureMarkdown, sample: str) -> tuple[object, ...]:
    try:
        html = md.reset().convert(sample)
    except ValueError as error:
        return type(error), error.args
    return html, tuple(md.htmlStash.rawHtmlBlocks), md.htmlStash.html_counter


if __name__ == "__main__":
    unittest.main()
