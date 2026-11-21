import sys
from importlib import import_module
from types import SimpleNamespace
from typing import SupportsIndex
from unittest import SkipTest
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from markdown import Markdown
from markdown.extensions.codehilite import CodeHiliteExtension
from typing_extensions import override

from zerver.lib.markdown import fenced_code


class CodeHeaderRustTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "parse_fenced_code_header"):
            raise SkipTest("Rust extension does not support code headers")

    def test_header_preserves_source_language_and_line_options(self) -> None:
        for source, language, linenos, lines in [
            ("#!/bin/PYTHON\n<x>&", "python", True, []),
            ("#!/usr/bin/env python\nprint(1)", "python", True, []),
            (":::Python hl_lines='۱ ٢ +3 -4 1_0'", "python", None, [1, 2, 3, -4, 10]),
            (":::python hl_lines='1 nope 3'", "python", None, []),
            (':::İΣ hl_lines="1\'2"', "i\u0307ς", None, []),
            ("#!", "", True, []),
            ("\n#!python\n", "initial", None, [9]),
        ]:
            for native in [None, self.extension]:
                with self.subTest(source=source, native=bool(native)):
                    highlighter = fenced_code.CodeHilite(source, lang="initial", hl_lines=[9])
                    with patch.object(fenced_code, "_rust_code_header", native):
                        highlighter._parseHeader()
                    self.assertEqual(highlighter.lang, language)
                    self.assertEqual(highlighter.options["linenos"], linenos)
                    self.assertEqual(highlighter.options["hl_lines"], lines)
                    self.assertEqual(highlighter.src, source.strip("\n"))

    def test_explicit_line_number_options_are_preserved(self) -> None:
        with patch.object(fenced_code, "_rust_code_header", self.extension):
            for linenos in [False, True]:
                highlighter = fenced_code.CodeHilite("#!python", linenums=linenos)
                highlighter._parseHeader()
                self.assertIs(highlighter.options["linenos"], linenos)

    def test_parse_failure_preserves_partial_state_without_retry(self) -> None:
        highlighter = fenced_code.CodeHilite("#!PYTHON hl_lines='1'\n\n")
        with (
            patch.object(fenced_code, "_rust_code_header", self.extension),
            patch.object(fenced_code, "parse_hl_lines", side_effect=ValueError("lines")) as parse,
            self.assertRaisesRegex(ValueError, "lines"),
        ):
            highlighter._parseHeader()
        parse.assert_called_once_with("1")
        self.assertEqual(highlighter.lang, "python")
        self.assertIs(highlighter.options["linenos"], True)
        self.assertEqual(highlighter.src, "#!PYTHON hl_lines='1'\n\n")

    def test_missing_options_fail_after_language_assignment(self) -> None:
        highlighter = fenced_code.CodeHilite(":::PYTHON\n")
        del highlighter.options["linenos"]
        with (
            patch.object(fenced_code, "_rust_code_header", self.extension),
            self.assertRaisesRegex(KeyError, "linenos"),
        ):
            highlighter._parseHeader()
        self.assertEqual(highlighter.lang, "python")
        self.assertEqual(highlighter.src, ":::PYTHON\n")

    def test_unsupported_source_and_old_wheel_delegate(self) -> None:
        for native in [SimpleNamespace(), self.extension]:
            for source in [
                "#!PYTHON\n\ud800",
                ":::PYTHON\n",
                "#!PYTHON\n" + "x" * (8 * 1024 * 1024),
            ]:
                with self.subTest(native=native, source=source[:20]):
                    highlighter = fenced_code.CodeHilite(source)
                    with patch.object(fenced_code, "_rust_code_header", native):
                        highlighter._parseHeader()
                    self.assertEqual(highlighter.lang, "python")
                    self.assertEqual(highlighter.src, source.strip("\n"))
        self.assertIsNone(self.extension.parse_fenced_code_header("#!python", "14.0.0"))

    def test_string_subclasses_keep_their_split_behavior(self) -> None:
        class Source(str):
            __slots__ = ()

            @override
            def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
                return [":::custom", "body"]

        highlighter = fenced_code.CodeHilite("original")
        highlighter.src = Source("original")
        with patch.object(fenced_code, "_rust_code_header", self.extension):
            highlighter._parseHeader()
        self.assertEqual(highlighter.lang, "custom")
        self.assertEqual(highlighter.src, ":::custom\nbody")

    def test_non_reference_runtime_delegates_before_parser(self) -> None:
        parser = Mock(side_effect=AssertionError("native parser called"))
        with (
            patch.object(
                fenced_code, "_rust_code_header", SimpleNamespace(parse_fenced_code_header=parser)
            ),
            patch.object(sys, "version_info", (3, 13)),
        ):
            highlighter = fenced_code.CodeHilite(":::PYTHON")
            highlighter._parseHeader()
        parser.assert_not_called()
        self.assertEqual(highlighter.lang, "python")

    def test_complete_fenced_render_matches_with_highlight_bridge(self) -> None:
        for text in [
            "```\n#!/usr/bin/python\nprint('<script>&')\n```",
            "```\n:::python hl_lines='1 2'\nprint(1)\n```",
            "```text\n#!python\n<x>&\n```",
            "```\n!important\n```",
            "~~~quote\n```\n:::İΣ\n<&>\n```\n~~~",
        ]:
            outputs = []
            for native in [None, self.extension]:
                with (
                    patch.object(fenced_code, "_rust_code_header", native),
                    patch.object(fenced_code, "_rust_code_highlight", native),
                ):
                    md = Markdown(
                        extensions=[
                            CodeHiliteExtension(guess_lang=False),
                            fenced_code.FencedCodeExtension(),
                        ]
                    )
                    outputs.append(md.convert(text))
            self.assertEqual(*outputs)
