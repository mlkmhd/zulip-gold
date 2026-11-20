import json
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest import SkipTest, TestCase
from unittest.mock import patch

import lxml.html
from typing_extensions import override

from zerver.lib import tex
from zerver.lib.push_notifications import get_mobile_push_content


class RawLatexRustTest(TestCase):
    @override
    def setUp(self) -> None:
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "change_katex_to_raw_latex"):
            raise SkipTest("Rust extension does not support raw LaTeX conversion")

    def convert(self, html: str) -> lxml.html.HtmlElement:
        fragment = lxml.html.fragment_fromstring(html, create_parent=True)
        with patch.object(tex, "_rust_raw_latex", self.extension):
            tex.change_katex_to_raw_latex(fragment)
        return fragment

    def test_math_text_is_escaped_and_tails_are_preserved(self) -> None:
        fragment = self.convert(
            '<p>before <span class="katex"><annotation encoding="application/x-tex">'
            "&lt;script&gt;&amp; فارسی α 😀</annotation><b>visual</b></span> after "
            '<span class="katex"><annotation encoding="application/x-tex">y</annotation>'
            "</span> last</p>"
        )
        self.assertEqual(
            lxml.html.tostring(fragment, encoding="unicode"),
            "<div><p>before <span>$$&lt;script&gt;&amp; فارسی α 😀$$</span> after "
            "<span>$$y$$</span> last</p></div>",
        )

    def test_exact_class_and_first_annotation_text(self) -> None:
        fragment = self.convert(
            '<span class="katex extra">unchanged</span>'
            '<span class="katex"><annotation encoding="other">wrong</annotation>'
            '<annotation encoding="application/x-tex">first<b>ignored</b>tail</annotation>'
            '<annotation encoding="application/x-tex">second</annotation></span>'
        )
        self.assertEqual(fragment[0].attrib, {"class": "katex extra"})
        self.assertEqual(fragment[0].text, "unchanged")
        self.assertEqual(fragment[1].text, "$$first$$")
        self.assertEqual(list(fragment[1]), [])

    def test_none_annotation_and_tail_values(self) -> None:
        for tail in [None, "", " after"]:
            with self.subTest(tail=tail):
                fragment = lxml.html.fragment_fromstring(
                    '<span class="katex"><annotation encoding="application/x-tex"/></span>',
                    create_parent=True,
                )
                fragment[0].tail = tail
                original = fragment[0]
                with patch.object(tex, "_rust_raw_latex", self.extension):
                    tex.change_katex_to_raw_latex(fragment)
                self.assertEqual(fragment[0].text, "$$None$$")
                self.assertEqual(fragment[0].tail, tail)
                self.assertIsNone(original.getparent())

    def test_absolute_xpath_includes_siblings_outside_fragment(self) -> None:
        fragment = lxml.html.fragment_fromstring(
            '<p>selected</p><span class="katex">'
            '<annotation encoding="application/x-tex">outside</annotation></span>',
            create_parent=True,
        )
        selected = fragment[0]
        with patch.object(tex, "_rust_raw_latex", self.extension):
            tex.change_katex_to_raw_latex(selected)
        self.assertIs(fragment[0], selected)
        self.assertEqual(fragment[1].text, "$$outside$$")

    def test_nested_matches_use_original_xpath_snapshot(self) -> None:
        fragment = self.convert(
            '<span class="katex"><span class="katex">'
            '<annotation encoding="application/x-tex">inner</annotation></span>'
            '<annotation encoding="application/x-tex">outer</annotation></span>'
        )
        self.assertEqual(lxml.html.tostring(fragment), b"<div><span>$$inner$$</span></div>")

    def test_missing_annotation_keeps_prior_replacements_without_retry(self) -> None:
        html = (
            '<span class="katex"><annotation encoding="application/x-tex">a</annotation>'
            '</span> between <span class="katex">missing</span>'
        )
        for native in [None, self.extension]:
            with self.subTest(native=native):
                fragment = lxml.html.fragment_fromstring(html, create_parent=True)
                with (
                    patch.object(tex, "_rust_raw_latex", native),
                    self.assertRaisesRegex(IndexError, "list index out of range"),
                ):
                    tex.change_katex_to_raw_latex(fragment)
                self.assertEqual(
                    lxml.html.tostring(fragment),
                    b'<div><span>$$a$$</span> between <span class="katex">missing</span></div>',
                )

    def test_root_match_propagates_parent_error(self) -> None:
        fragment = lxml.html.Element("span", {"class": "katex"})
        annotation = lxml.html.Element("annotation", {"encoding": "application/x-tex"})
        annotation.text = "x"
        fragment.append(annotation)
        before = lxml.html.tostring(fragment)
        with (
            patch.object(tex, "_rust_raw_latex", self.extension),
            self.assertRaisesRegex(AttributeError, "'NoneType' object has no attribute 'replace'"),
        ):
            tex.change_katex_to_raw_latex(fragment)
        self.assertEqual(lxml.html.tostring(fragment), before)

    def test_disabled_and_old_wheel_fallback(self) -> None:
        for native in [None, SimpleNamespace()]:
            with self.subTest(native=native):
                fragment = lxml.html.fragment_fromstring(
                    '<span class="katex"><annotation encoding="application/x-tex">x</annotation>'
                    "</span> after",
                    create_parent=True,
                )
                with patch.object(tex, "_rust_raw_latex", native):
                    tex.change_katex_to_raw_latex(fragment)
                self.assertEqual(
                    lxml.html.tostring(fragment), b"<div><span>$$x$$</span> after</div>"
                )

    def test_bridge_exception_does_not_retry(self) -> None:
        fragment = lxml.html.Element("div")
        failure = RuntimeError("replacement failed")
        with (
            patch.object(tex, "_rust_raw_latex", self.extension),
            patch.object(
                self.extension, "change_katex_to_raw_latex", side_effect=failure
            ) as convert,
            self.assertRaises(RuntimeError) as raised,
        ):
            tex.change_katex_to_raw_latex(fragment)
        self.assertIs(raised.exception, failure)
        convert.assert_called_once()
        self.assertIs(convert.call_args.args[0], fragment)
        self.assertIs(convert.call_args.args[1], vars(tex))

    def test_real_katex_fixtures_and_push_text(self) -> None:
        fixtures = json.loads(
            (Path(__file__).parent / "fixtures/markdown_test_cases.json").read_text()
        )
        checked = 0
        for fixture in fixtures["regular_tests"]:
            html = fixture.get("expected_output", "")
            if 'class="katex"' not in html:
                continue
            with self.subTest(fixture=fixture["name"]):
                reference = lxml.html.fragment_fromstring(html, create_parent=True)
                with patch.object(tex, "_rust_raw_latex", None):
                    tex.change_katex_to_raw_latex(reference)
                    expected_push = get_mobile_push_content(html)
                with (
                    patch.object(tex, "_rust_raw_latex", self.extension),
                    patch.object(
                        self.extension,
                        "change_katex_to_raw_latex",
                        wraps=self.extension.change_katex_to_raw_latex,
                    ) as convert,
                ):
                    actual = self.convert(html)
                    self.assertEqual(get_mobile_push_content(html), expected_push)
                    self.assertEqual(convert.call_count, 2)
                self.assertEqual(lxml.html.tostring(actual), lxml.html.tostring(reference))
                self.assertNotIn('class="katex"', lxml.html.tostring(actual, encoding="unicode"))
                if "text_content" in fixture:
                    self.assertEqual(expected_push, fixture["text_content"])
                checked += 1
        self.assertGreater(checked, 0)
