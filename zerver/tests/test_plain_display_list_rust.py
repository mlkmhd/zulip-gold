import os
from importlib import import_module
from unittest import TestCase
from unittest.mock import call, patch

from django.template import engines
from django.template.backends.jinja2 import Jinja2
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils.safestring import SafeString
from markupsafe import Markup

from zerver.lib import templates
from zerver.lib.native_backend import supports_default_rust_backend


class PlainDisplayListRustTest(TestCase):
    def test_exact_legacy_joining(self) -> None:
        try:
            backend = import_module("_zulip_backend")
            present = backend.format_plain_display_list
        except (ImportError, AttributeError):
            self.skipTest("Rust plain display list helper is not available")

        one = "value:synthetic".removeprefix("value:")
        self.assertIs(present([one], -8), one)
        self.assertEqual(present(["é", "<&>", "😀"], 3), "é, <&> and 😀")
        self.assertEqual(present(["é", "<&>", "😀"], 2), "é, <&> and 1 other")
        self.assertEqual(present(["a", "b", "c"], -1), "a, b and 4 others")
        self.assertEqual(present(["a", "b", "c"], -5), " and 8 others")
        self.assertEqual(present([], -1), " and 1 other")
        self.assertEqual(present(["a", "b"], 0), " and 2 others")
        self.assertEqual(present(["a", "b"], -2), " and 4 others")
        self.assertIs(type(present(["<&>", "b"], 1)), str)

        if getattr(backend, "PLAIN_DISPLAY_LIST_API", None) == 1:
            special = "".join(map(chr, range(0xD800, 0xE000))) + "🙂"
            self.assertIs(present([special], 4), special)
            values = [special, "plain", special[::-1]]
            for limit in (-1, 0, 1, 2, 3, 4):
                self.assertEqual(present(values, limit), templates.display_list(values, limit))

        for values, limit in (
            ([], 0),
            ([Markup("a")], 99),
            (["a", Markup("b")], 1),
            (["a", 9], 1),
            (("a", "b"), 1),
            (["a", "b"], 2**100),
            (["a", "b"], -(2**63)),
            (["a", "b"], True),
        ):
            self.assertIsNone(present(values, limit))


class PlainDisplayListIntegrationTest(SimpleTestCase):
    def test_real_plain_digest_uses_only_its_own_filter(self) -> None:
        try:
            backend = import_module("_zulip_backend")
        except ImportError:
            backend = None
        enabled = backend is not None and (
            os.environ.get("ZULIP_RUST_PLAIN_DISPLAY_LIST") == "1"
            or (
                supports_default_rust_backend()
                and getattr(backend, "PLAIN_DISPLAY_LIST_API", None) == 1
            )
        )
        helper = templates._rust_format_plain_display_list
        if enabled:
            self.assertIsNotNone(helper)
        else:
            self.assertIsNone(helper)
        native_spy = (
            patch.object(templates, "_rust_format_plain_display_list", wraps=helper)
            if enabled
            else patch.object(templates, "_rust_format_plain_display_list", None)
        )
        plaintext = engines["Jinja2_plaintext"]
        html = engines["Jinja2"]
        assert isinstance(plaintext, Jinja2)
        assert isinstance(html, Jinja2)
        self.assertIs(plaintext.env.filters["plain_display_list"], templates.plain_display_list)
        self.assertIs(html.env.filters["display_list"], templates.display_list)
        self.assertIs(html.env.filters["plain_display_list"], templates.display_list)

        participants = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
        channels = ["Synthetic room", "Second room"]
        context = {
            "show_message_content": True,
            "hot_conversations": [
                {
                    "count": 1,
                    "participants": participants,
                    "first_few_messages": {"header": {"plain": "Preview header"}, "senders": []},
                }
            ],
            "new_channels": {"plain": channels},
            "realm_url": "https://example.invalid",
            "unsubscribe_link": "https://example.invalid/example",
        }
        template = "zerver/emails/digest.txt"
        with (
            native_spy as native,
            patch("django.core.mail.EmailMessage.send", side_effect=AssertionError("email sent")),
        ):
            result = render_to_string(template, context, using="Jinja2_plaintext")
            if enabled:
                assert native is not None
                self.assertEqual(
                    native.call_args_list, [call(participants, 4), call(channels, 1000)]
                )
            with patch.dict(plaintext.env.filters, {"plain_display_list": templates.display_list}):
                self.assertEqual(
                    result, render_to_string(template, context, using="Jinja2_plaintext")
                )
        self.assertIn("Alpha, Beta, Gamma, Delta and 1 other.", result)
        self.assertIn("Synthetic room and Second room.", result)

        context["new_channels"] = {"plain": ["Research & design", "Second room"]}
        preview = render_to_string(template, context, using="Jinja2")
        self.assertIn("Research &amp; design and Second room.", preview)

    def test_markup_and_subclasses_fall_back_with_original_identity(self) -> None:
        marked = Markup("<b>synthetic</b>")
        safe = SafeString("safe synthetic")

        class Derived(str):
            __slots__ = ()

        custom = Derived("synthetic subclass")
        for value in (marked, safe, custom):
            self.assertIs(templates.plain_display_list([value], 9), value)
        mixed: list[str | Markup] = [marked, "plain", safe]
        expected = templates.display_list(mixed, 2)
        actual = templates.plain_display_list(mixed, 2)
        self.assertEqual(actual, expected)
        self.assertIs(type(actual), type(expected))

    def test_plain_limit_fallback_and_legacy_empty_error(self) -> None:
        plain = "value:word".removeprefix("value:")
        self.assertIs(templates.plain_display_list([plain], 2**100), plain)
        self.assertIs(templates.plain_display_list([plain], -3), plain)
        for limit in (-1, 0, True, 2**100, -(2**63)):
            self.assertEqual(
                templates.plain_display_list(["a", "b", "c"], limit),
                templates.display_list(["a", "b", "c"], limit),
            )
        self.assertEqual(templates.plain_display_list([], -1), " and 1 other")
        with self.assertRaisesRegex(IndexError, "list index out of range"):
            templates.plain_display_list([], 0)
