# ruff: noqa: INP001

"""Exercise the source formatter without importing the provisioned Zulip app."""

import ast
import random
import unittest
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = import_module("_zulip_backend")
SOURCE = ROOT / "zerver/lib/markdown/fenced_code.py"


def load_formatter(
    backend: object, name: str = "format_quote", *, owner: str = "FencedBlockPreprocessor"
) -> Callable[..., object]:
    module = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    cls = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == owner
    )
    method = next(
        node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace = {"_rust_markdown_fences": backend, "cast": cast}
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


if __name__ == "__main__":
    unittest.main()
