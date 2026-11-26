from importlib import import_module
from os import environ
from random import Random
from types import ModuleType, SimpleNamespace
from typing import SupportsIndex
from unittest import SkipTest
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from typing_extensions import override

from tools.setup.emoji.emoji_setup_utils import get_emoji_code
from zerver.lib import emoji_utils
from zerver.lib.markdown import inline_stash, markdown_convert
from zerver.lib.push_notifications import get_mobile_push_content
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message


def extension() -> ModuleType:
    try:
        native = import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        raise SkipTest("Rust extension is not installed") from None
    for name in ("unqualify_emoji_text", "encode_emoji_codepoints", "decode_emoji_codepoints"):
        if not callable(getattr(native, name, None)):
            raise SkipTest("Rust extension does not support emoji codepoints")
    return native


class EmojiCodepointsRustTest(SimpleTestCase):
    def test_scalar_conversion_and_qualification(self) -> None:
        native = extension()
        rng = Random(10304)
        fragments = (
            "",
            "\0",
            "سلام",
            "\u200d",
            "🙂",
            "\ufe0f",
            "\ufe0e",
            "\U000e0067",
            "\U0010ffff",
        )
        for length in range(17):
            for _ in range(20):
                text = "".join(rng.choices(fragments, k=length))
                self.assertEqual(native.unqualify_emoji_text(text), text.replace("\ufe0f", ""))
                expected = "-".join(f"{ord(character):04x}" for character in text)
                self.assertEqual(native.encode_emoji_codepoints(text), expected)
                if text:
                    self.assertEqual(native.decode_emoji_codepoints(expected.upper()), text)
        self.assertEqual(native.unqualify_emoji_text("✋\ufe0e\ufe0f"), "✋\ufe0e")
        self.assertEqual(native.decode_emoji_codepoints("0-41-1F642"), "\0A🙂")

    def test_declines_legacy_numeric_syntax_and_surrogate_values(self) -> None:
        native = extension()

        class EmojiString(str):
            __slots__ = ()

        for unusual in ("\ud800", None, b"x", EmojiString("a")):
            self.assertIsNone(native.unqualify_emoji_text(unusual))
            self.assertIsNone(native.encode_emoji_codepoints(unusual))
        for unusual in ("", "0x41", "+41", " 41 ", "0_041", "４１", "41-", "d800", "110000"):
            self.assertIsNone(native.decode_emoji_codepoints(unusual))
        self.assertIsNone(native.decode_emoji_codepoints(EmojiString("41")))

    def test_utility_fallback_keeps_legacy_results_and_errors(self) -> None:
        backend = extension()
        patcher = patch.object(emoji_utils, "_rust_emoji_selector", None)
        patcher.start()
        self.addCleanup(patcher.stop)

        def historical() -> tuple[list[str], list[str]]:
            values = [
                emoji_utils.unqualify_emoji("✋\ufe0e\ufe0f\ud800"),
                emoji_utils.emoji_to_hex_codepoint("A\ud800🙂"),
                emoji_utils.hex_codepoint_to_emoji("0x41-+0042- 43 -0_044-４５-d800"),
            ]
            errors = []
            for invalid in ("", "41-", "110000", "not hex"):
                with self.assertRaises(ValueError) as error:
                    emoji_utils.hex_codepoint_to_emoji(invalid)
                errors.append(str(error.exception))
            return values, errors

        with patch.object(emoji_utils, "_backend", None):
            expected = historical()
        self.assertEqual(expected[0], ["✋\ufe0e\ud800", "0041-d800-1f642", "ABCDE\ud800"])
        with patch.object(emoji_utils, "_backend", backend):
            self.assertEqual(historical(), expected)

        old = SimpleNamespace(unqualify_emoji_text=backend.unqualify_emoji_text)
        decline = Mock(return_value=None)
        unsupported = SimpleNamespace(
            unqualify_emoji_text=decline,
            encode_emoji_codepoints=decline,
            decode_emoji_codepoints=decline,
        )
        for candidate in (old, unsupported):
            with patch.object(emoji_utils, "_backend", candidate):
                self.assertEqual(historical(), expected)
        self.assertEqual(decline.call_count, 7)


class EmojiCodepointDjangoDispatchRustTest(ZulipTestCase):
    def test_markdown_and_push_text_use_real_emoji_helpers(self) -> None:
        backend = extension()
        enabled = environ.get("ZULIP_RUST_EMOJI_CODEPOINTS") == "1"
        selector_enabled = emoji_utils._rust_emoji_selector is not None
        actor = self.example_user("hamlet")
        message = Message(sender=actor, realm=actor.realm)

        def render() -> tuple[str, str]:
            # Keep the Python Markdown inline processor active to witness
            # its normal emoji helper calls independently of its own flag.
            with patch.object(inline_stash, "_rust_native_inline", None):
                markup = markdown_convert(
                    "☕\ufe0f", message_realm=actor.realm, message=message
                ).rendered_content
            return markup, get_mobile_push_content(markup)

        with (
            patch.object(emoji_utils, "_backend", None),
            patch.object(emoji_utils, "_rust_emoji_selector", None),
        ):
            expected = render()
        self.assertIn('class="emoji emoji-2615"', expected[0])
        self.assertEqual(expected[1], "☕")

        accepted: list[str] = []

        def witness(symbol: str, value: str) -> str | None:
            result: str | None = native_calls[symbol](value)
            if result is not None:
                accepted.append(symbol)
            return result

        native_calls = {
            "unqualify_emoji_text": backend.unqualify_emoji_text,
            "encode_emoji_codepoints": backend.encode_emoji_codepoints,
            "decode_emoji_codepoints": backend.decode_emoji_codepoints,
        }
        if selector_enabled:
            native_calls["unqualify_exact_emoji_text"] = backend.unqualify_exact_emoji_text
        with (
            patch.object(
                backend,
                "unqualify_exact_emoji_text",
                create=True,
                side_effect=lambda value: witness("unqualify_exact_emoji_text", value),
            ),
            patch.object(
                backend,
                "unqualify_emoji_text",
                side_effect=lambda value: witness("unqualify_emoji_text", value),
            ),
            patch.object(
                backend,
                "encode_emoji_codepoints",
                side_effect=lambda value: witness("encode_emoji_codepoints", value),
            ),
            patch.object(
                backend,
                "decode_emoji_codepoints",
                side_effect=lambda value: witness("decode_emoji_codepoints", value),
            ),
        ):
            self.assertEqual(render(), expected)
        expected_calls = []
        if selector_enabled:
            expected_calls.append("unqualify_exact_emoji_text")
        elif enabled:
            expected_calls.append("unqualify_emoji_text")
        if enabled:
            expected_calls.extend(["encode_emoji_codepoints", "decode_emoji_codepoints"])
        self.assertEqual(accepted, expected_calls)

        with patch.object(emoji_utils, "_backend", SimpleNamespace()):
            self.assertEqual(render(), expected)


class EmojiPresentationSelectorContractTest(SimpleTestCase):
    def test_catalog_keeps_text_selector_and_removes_only_emoji_selector(self) -> None:
        self.assertEqual(get_emoji_code({"unified": "2615-FE0F-FE0E"}), "2615-fe0e")
        self.assertEqual(get_emoji_code({"unified": "1F469-FE0F-200D-1F4BB"}), "1f469-200d-1f4bb")

    def test_current_wheel_preserves_custom_unicode_and_noop_identity(self) -> None:
        backend = emoji_utils._rust_emoji_selector
        if getattr(backend, "EMOJI_PRESENTATION_SELECTOR_API", None) != 1:
            self.skipTest("Matching emoji presentation selector contract is not available")

        original = "✋\ufe0e\ud800"
        with patch.object(
            backend, "unqualify_exact_emoji_text", wraps=backend.unqualify_exact_emoji_text
        ) as unqualify:
            self.assertIs(emoji_utils.unqualify_emoji(original), original)
            self.assertEqual(emoji_utils.unqualify_emoji("\ufe0f\ufe0f" + original), original)
        self.assertEqual(unqualify.call_count, 2)

    def test_custom_replace_remains_in_python(self) -> None:
        class CustomEmoji(str):
            __slots__ = ()

            @override
            def replace(self, old: str, new: str, count: SupportsIndex = -1) -> str:
                return "custom"

        self.assertEqual(emoji_utils.unqualify_emoji(CustomEmoji("☕\ufe0f")), "custom")
