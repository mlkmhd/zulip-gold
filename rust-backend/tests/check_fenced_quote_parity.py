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


def load_formatter(backend: object) -> Callable[[object, str], str]:
    module = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    cls = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "FencedBlockPreprocessor"
    )
    method = next(
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "format_quote"
    )
    namespace = {"_rust_markdown_fences": backend, "cast": cast}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), namespace)  # noqa: S102
    return cast(Callable[[object, str], str], namespace["format_quote"])


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


if __name__ == "__main__":
    unittest.main()
