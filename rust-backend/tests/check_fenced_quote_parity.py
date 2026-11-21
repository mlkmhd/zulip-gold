# ruff: noqa: INP001

"""Exercise the source formatter without importing the provisioned Zulip app."""

import ast
import random
import re
import unittest
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

from markdown import Markdown
from markdown.preprocessors import Preprocessor
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
    namespace = {"_rust_markdown_fences": backend, "cast": cast, "override": override} | (
        extra or {}
    )
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
        for text in fixtures + cases:
            result = reference(None, text)
            self.assertEqual(old_wheel(None, text), result)
            self.assertEqual(opted_in(None, text), result)
        self.assertEqual(native.call_count, len(fixtures) + len(cases))
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
            self.assertEqual(len(seen), 3)
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

        def check(*args: object, **kwargs: object) -> None:
            self.assertEqual(len(args), 3)
            self.assertIs(args[0], processor)
            self.assertIs(args[1], output)
            self.assertIs(args[2], line)
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


def load_fence_stack(backend: object) -> dict[str, object]:
    source = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    future = ast.parse("from __future__ import annotations").body
    names = {
        "FENCE_RE",
        "_original_parse_fence_line",
        "CODE_VALIDATORS",
        "parse_fence_line",
        "check_for_new_fence",
        "generic_handler",
        "validate_curl_content",
        "ZulipBaseHandler",
        "OuterHandler",
        "QuoteHandler",
        "SpoilerHandler",
        "CodeHandler",
        "TexHandler",
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
        "_rust_markdown_blocks": None,
        "cast": cast,
        "override": override,
        "re": re,
        "Preprocessor": Preprocessor,
        "PLAYGROUND_LANGUAGE_REGEX": language,
        "MarkdownRenderingError": ValueError,
    }
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


if __name__ == "__main__":
    unittest.main()
