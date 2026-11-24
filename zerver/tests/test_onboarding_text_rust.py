import re
from importlib import import_module
from os import environ
from random import Random
from types import ModuleType
from typing import cast
from unittest import SkipTest
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import message, native_onboarding_text, onboarding, zulip_update_announcements
from zerver.lib.zulip_update_announcements import ZulipUpdateAnnouncement
from zerver.models import Realm
from zerver.models.groups import SystemGroups


def extension() -> ModuleType:
    try:
        native = import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        raise SkipTest("Rust extension is not installed") from None
    for name in ("flatten_prepared_onboarding_newlines", "wrap_prepared_welcome_quote"):
        if not callable(getattr(native, name, None)):
            raise SkipTest("Rust extension does not support prepared onboarding text")
    return native


class OnboardingTextRustTest(SimpleTestCase):
    def test_only_replaces_existing_single_lf_lookarounds(self) -> None:
        native = extension()
        text = "\n\nآزمایش\n123. item\n* bullet\na\n  - indented\r\n\n🙂\n"
        expected = "آزمایش\n123. item\n* bullet a - indented\r\n\n🙂"
        self.assertEqual(native.flatten_prepared_onboarding_newlines(text), expected)
        unchanged = "سلام\u0085🙂\r\t"
        self.assertIs(native.flatten_prepared_onboarding_newlines(unchanged), unchanged)

        rng = Random(6702)
        parts = ("\n", "\n\n", "  ", "\r", "\t", "  - ", "* ", "1. ", "۱. ", "🙂", "سلام")
        for _ in range(500):
            text = "".join(rng.choices(parts, k=rng.randrange(70)))
            expected = re.sub(r"(?<!\n)\n(?!\n|[-*] |[0-9]+\. ) *", " ", text.strip("\n"))
            self.assertEqual(native.flatten_prepared_onboarding_newlines(text), expected)

    def test_exact_welcome_wrapper_and_legacy_decline(self) -> None:
        native = extension()
        intro, fence, body = "آزمایش 🙂:", "\x60\x60\x60", "  content\r\n\0"
        expected = f"\n{intro}\n{fence}quote\n{body}\n{fence}\n"
        self.assertEqual(native.wrap_prepared_welcome_quote(intro, fence, body), expected)
        self.assertEqual(native.wrap_prepared_welcome_quote("", "", ""), "\n\nquote\n\n\n")

        class TextSubclass(str):
            __slots__ = ()

        for unusual in ("\ud800", None, b"text", TextSubclass("synthetic")):
            self.assertIsNone(native.flatten_prepared_onboarding_newlines(unusual))
            self.assertIsNone(native.wrap_prepared_welcome_quote(unusual, fence, body))
            self.assertIsNone(native.wrap_prepared_welcome_quote(intro, unusual, body))
            self.assertIsNone(native.wrap_prepared_welcome_quote(intro, fence, unusual))


class OnboardingTextDjangoDispatchRustTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        for target, entry in (
            (onboarding, "do_send_messages"),
            (onboarding, "internal_send_private_message"),
            (zulip_update_announcements, "do_send_messages"),
        ):
            send_patch = patch.object(
                target, entry, side_effect=AssertionError("text building must not send messages")
            )
            send_patch.start()
            self.addCleanup(send_patch.stop)

    def test_real_announcement_and_welcome_builders_use_prepared_inputs_only(self) -> None:
        native = extension()
        enabled = environ.get("ZULIP_RUST_ONBOARDING_TEXT") == "1"
        flatten = native_onboarding_text.flatten_prepared_onboarding_newlines
        wrap = native_onboarding_text.wrap_prepared_welcome_quote
        self.assertIs(flatten, native.flatten_prepared_onboarding_newlines if enabled else None)
        self.assertIs(wrap, native.wrap_prepared_welcome_quote if enabled else None)
        flatten_call = Mock(wraps=flatten)
        wrap_call = Mock(wraps=wrap)
        announcement = "\nآزمایش\n* item\nfollow-up\n\n🙂\n"
        body = "```\nسلام🙂\r\n```\n"
        expected_intro = f"یادداشت @_*{SystemGroups.ADMINISTRATORS}* :"
        selected_fence = "````"

        with (
            patch.object(
                zulip_update_announcements,
                "zulip_update_announcements",
                [ZulipUpdateAnnouncement(level=1, message=announcement)],
            ),
            patch.object(
                onboarding, "_", return_value="یادداشت {admin_group_syntax} :"
            ) as translate,
            patch.object(
                native_onboarding_text,
                "flatten_prepared_onboarding_newlines",
                flatten_call if enabled else None,
            ),
            patch.object(
                native_onboarding_text,
                "wrap_prepared_welcome_quote",
                wrap_call if enabled else None,
            ),
        ):
            self.assertEqual(
                zulip_update_announcements.get_zulip_update_announcements_message_for_level(1),
                "آزمایش\n* item follow-up\n\n🙂",
            )
            self.assertEqual(
                onboarding.get_custom_welcome_message_string(Realm(string_id="synthetic"), body),
                f"\n{expected_intro}\n{selected_fence}quote\n{body}\n{selected_fence}\n",
            )
        translate.assert_called_once_with("A note from {admin_group_syntax}:")
        if enabled:
            flatten_call.assert_called_once_with(announcement)
            wrap_call.assert_called_once_with(expected_intro, selected_fence, body)
        else:
            flatten_call.assert_not_called()
            wrap_call.assert_not_called()

    def test_optional_helpers_can_decline_or_return_empty_independently(self) -> None:
        realm = Realm(string_id="synthetic")
        body = "synthetic"
        with (
            patch.object(native_onboarding_text, "flatten_prepared_onboarding_newlines", None),
            patch.object(native_onboarding_text, "wrap_prepared_welcome_quote", None),
        ):
            expected_welcome = onboarding.get_custom_welcome_message_string(realm, body)
        flatten = Mock(return_value="")
        wrap = Mock(return_value="")
        decline_flatten = Mock(return_value=None)
        decline_wrap = Mock(return_value=None)
        for first, second, expected_flatten, expected_wrap in (
            (None, None, "line two", expected_welcome),
            (flatten, None, "", expected_welcome),
            (None, wrap, "line two", ""),
            (decline_flatten, decline_wrap, "line two", expected_welcome),
        ):
            with (
                patch.object(native_onboarding_text, "flatten_prepared_onboarding_newlines", first),
                patch.object(native_onboarding_text, "wrap_prepared_welcome_quote", second),
            ):
                self.assertEqual(message.remove_single_newlines("line\ntwo"), expected_flatten)
                self.assertEqual(
                    onboarding.get_custom_welcome_message_string(realm, body), expected_wrap
                )
        flatten.assert_called_once_with("line\ntwo")
        wrap.assert_called_once()
        decline_flatten.assert_called_once_with("line\ntwo")
        decline_wrap.assert_called_once()

    def test_native_decline_preserves_surrogates_and_legacy_overrides(self) -> None:
        class TextSubclass(str):
            __slots__ = ()

            @override
            def strip(self, chars: str | None = None, /) -> str:
                return "historical\noverride"

            @override
            def __format__(self, format_spec: str, /) -> str:
                return "historical formatting"

        native = extension()
        realm = Realm(string_id="synthetic")
        snapshots = []
        for flatten, wrap in (
            (None, None),
            (native.flatten_prepared_onboarding_newlines, native.wrap_prepared_welcome_quote),
        ):
            with (
                patch.object(
                    native_onboarding_text, "flatten_prepared_onboarding_newlines", flatten
                ),
                patch.object(native_onboarding_text, "wrap_prepared_welcome_quote", wrap),
            ):
                self.assertEqual(
                    message.remove_single_newlines(TextSubclass("ignored")), "historical override"
                )
                self.assertEqual(message.remove_single_newlines("\ud800\n🙂"), "\ud800 🙂")
                with self.assertRaisesRegex(AttributeError, "has no attribute 'strip'"):
                    message.remove_single_newlines(cast("str", None))
                snapshots.append(
                    (
                        onboarding.get_custom_welcome_message_string(realm, TextSubclass("hello")),
                        onboarding.get_custom_welcome_message_string(realm, "\ud800"),
                    )
                )
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertIn("\nhistorical formatting\n", snapshots[1][0])
        self.assertIn("\n\ud800\n", snapshots[1][1])
