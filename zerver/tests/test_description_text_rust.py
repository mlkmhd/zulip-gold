import os
from importlib import import_module
from typing import SupportsIndex
from unittest import TestCase
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils.html import escape as django_escape
from django.utils.safestring import SafeString
from typing_extensions import override

from zerver.lib import html_to_text
from zerver.lib.native_backend import supports_default_rust_backend


class DescriptionTextRustTest(TestCase):
    def test_exact_python_whitespace_and_identity(self) -> None:
        try:
            module = import_module("_zulip_backend")
            normalize = module.normalize_description_text
        except (ImportError, AttributeError):
            self.skipTest("Rust description text normalization is unavailable")

        single_word = "value:فارسی😀".removeprefix("value:")
        self.assertIs(normalize(single_word), single_word)
        for source, expected in (
            ("", ""),
            (" \t\r\u2028\u3000", ""),
            ("\u001f  فارسی\u0085\t😀\u3000", "فارسی 😀"),
            ("\u200b\ufeff\0", "\u200b\ufeff\0"),
            ("one\u2029two\u001cthree\u202ffour", "one two three four"),
        ):
            self.assertEqual(normalize(source), expected)

        class Derived(str):
            __slots__ = ()

        for unsupported in (None, b"word", Derived("word")):
            self.assertIsNone(normalize(unsupported))

        if getattr(module, "DESCRIPTION_TEXT_API", None) == 1:
            surrogates = "".join(map(chr, range(0xD800, 0xE000)))
            self.assertIs(normalize(surrogates), surrogates)
            prepared = "\u2000 " + "\x1f\U0001f600\u2028".join(surrogates) + "\u3000"
            self.assertEqual(normalize(prepared), " ".join(prepared.split()))
            self.assertEqual(normalize("\ud800\t\udfff"), "\ud800 \udfff")


class DescriptionTextIntegrationTest(SimpleTestCase):
    def test_actual_description_extraction_and_native_selection(self) -> None:
        try:
            backend = import_module("_zulip_backend")
        except ImportError:
            backend = None
        enabled = backend is not None and (
            os.environ.get("ZULIP_RUST_DESCRIPTION_TEXT") == "1"
            or (
                supports_default_rust_backend()
                and getattr(backend, "DESCRIPTION_TEXT_API", None) == 1
            )
        )
        method = html_to_text._rust_normalize_description_text
        if enabled:
            self.assertIsNotNone(method)
        else:
            self.assertIsNone(method)
        native_spy = (
            patch.object(html_to_text, "_rust_normalize_description_text", wraps=method)
            if enabled
            else patch.object(html_to_text, "_rust_normalize_description_text", None)
        )
        content = (
            '<div class="admonition"><p>Excluded warning</p></div>'
            '<div class="tabbed-section"><p>Excluded navigation</p></div>'
            "<p>  A&nbsp; &amp; B </p><li>  فارسی    👋 </li><p></p>"
        )
        with (
            native_spy as native,
            patch.object(html_to_text, "escape", wraps=django_escape) as escape,
        ):
            result = html_to_text.html_to_text(content, {"p": " | ", "li": " * "})
            self.assertEqual(result, "A &amp; B * فارسی 👋")
            self.assertIsInstance(result, SafeString)
            escape.assert_called_once_with("A & B * فارسی 👋")
            if enabled:
                assert native is not None
                native.assert_called_once_with("  A\xa0 & B  *   فارسی    👋 ")

    def test_existing_description_cutoff_not_changed(self) -> None:
        long_first = "a" * 510
        self.assertEqual(
            html_to_text.html_to_text(f"<p>{long_first}</p><p>not included</p>"), long_first
        )

    def test_declined_text_retains_original_python_behavior(self) -> None:
        class CustomText(str):
            __slots__ = ()

            @override
            def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
                return ["Python", "subclass"]

        self.assertEqual(
            html_to_text._normalize_description_text(CustomText("ignored storage")),
            "Python subclass",
        )
        self.assertEqual(html_to_text._normalize_description_text("x\t\ud800  y"), "x \ud800 y")
