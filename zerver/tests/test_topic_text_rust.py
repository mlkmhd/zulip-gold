from importlib import import_module
from os import environ
from random import Random
from types import ModuleType
from typing import cast
from unittest import SkipTest
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import message, native_topic_text, topic


def extension() -> ModuleType:
    try:
        native = import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        raise SkipTest("Rust extension is not installed") from None
    for name in ("truncate_topic_title", "split_resolved_topic_title"):
        if not callable(getattr(native, name, None)):
            raise SkipTest("Rust extension does not support topic title formatting")
    return native


class TopicTextRustTest(SimpleTestCase):
    def test_resolution_prefix_is_literal_and_removed_once(self) -> None:
        native = extension()
        for title, expected in [
            ("✔ ", (True, "")),
            ("✔ ✔ آزمایش", (True, "✔ آزمایش")),
            ("✔ ✔\n", (True, "✔\n")),
            ("✔  room ", (True, " room ")),
            ("✔\ufe0f name", (False, "✔\ufe0f name")),
            ("✓ name", (False, "✓ name")),
            ("  ✔ name", (False, "  ✔ name")),
            ("\n✔ name", (False, "\n✔ name")),
            ("✔\u00a0name", (False, "✔\u00a0name")),
            ("", (False, "")),
        ]:
            actual = native.split_resolved_topic_title(title)
            self.assertEqual(actual, expected)
            if not expected[0]:
                self.assertIs(actual[1], title)

    def test_truncation_counts_codepoints_and_respects_signed_slice(self) -> None:
        native = extension()
        rng = Random(61034)
        parts = ("", "🙂", "آزمایش", "e\u0301", "\u200d", "\0", "\r\n", "✔ ")
        for limit in (-(2**63), -2, 0, 1, 2, 3, 4, 60, 2**63 - 1):
            for length in range(22):
                title = "".join(rng.choices(parts, k=length))
                expected = title[: limit - 3] + "..." if len(title) > limit else title
                actual = native.truncate_topic_title(title, limit)
                self.assertEqual(actual, expected)
                if len(title) <= limit:
                    self.assertIs(actual, title)
        self.assertEqual(native.truncate_topic_title("🙂" * 5, 2), "🙂" * 4 + "...")

    def test_noncanonical_objects_decline(self) -> None:
        native = extension()

        class TopicSubclass(str):
            __slots__ = ()

        for title in ("\ud800", None, b"title", TopicSubclass("✔ title")):
            self.assertIsNone(native.truncate_topic_title(title, 60))
            self.assertIsNone(native.split_resolved_topic_title(title))
        for limit in (True, "60", 60.0, 2**100):
            self.assertIsNone(native.truncate_topic_title("Title", limit))


class TopicTextDjangoDispatchRustTest(SimpleTestCase):
    def test_real_startup_mode_and_wrappers(self) -> None:
        native = extension()
        enabled = environ.get("ZULIP_RUST_TOPIC_TEXT") == "1"
        truncate = native_topic_text.truncate_topic_title
        split = native_topic_text.split_resolved_topic_title
        self.assertIs(truncate, native.truncate_topic_title if enabled else None)
        self.assertIs(split, native.split_resolved_topic_title if enabled else None)
        truncate_call = Mock(wraps=truncate)
        split_call = Mock(wraps=split)
        title = "✔ 🙂 سلام"
        with (
            patch.object(message, "MAX_TOPIC_NAME_LENGTH", 6),
            patch.object(
                native_topic_text, "truncate_topic_title", truncate_call if enabled else None
            ),
            patch.object(
                native_topic_text, "split_resolved_topic_title", split_call if enabled else None
            ),
        ):
            clipped = message.truncate_topic(title)
            self.assertEqual(clipped, "✔ 🙂...")
            self.assertEqual(topic.get_topic_resolution_and_bare_name(clipped), (True, "🙂..."))
        if enabled:
            truncate_call.assert_called_once_with(title, 6)
            split_call.assert_called_once_with(clipped)
        else:
            truncate_call.assert_not_called()
            split_call.assert_not_called()

    def test_each_helper_uses_results_and_falls_back_independently(self) -> None:
        title = "synthetic title"
        for clip_result, split_result, expected_clip, expected_split in (
            (None, None, title, (False, title)),
            ("", None, "", (False, title)),
            (None, (True, ""), title, (True, "")),
        ):
            truncate_call = Mock(return_value=clip_result)
            split_call = Mock(return_value=split_result)
            with (
                patch.object(native_topic_text, "truncate_topic_title", truncate_call),
                patch.object(native_topic_text, "split_resolved_topic_title", split_call),
            ):
                self.assertEqual(message.truncate_topic(title), expected_clip)
                actual_split = topic.get_topic_resolution_and_bare_name(title)
                self.assertEqual(actual_split, expected_split)
                if not actual_split[0]:
                    self.assertIs(actual_split[1], title)
            truncate_call.assert_called_once()
            split_call.assert_called_once_with(title)

    def test_patched_punctuation_and_malformed_limit_stay_python(self) -> None:
        class ShortSuffix(str):
            __slots__ = ()

            @override
            def __len__(self) -> int:
                return 1

        truncate = Mock()
        split = Mock()
        with (
            patch.object(native_topic_text, "truncate_topic_title", truncate),
            patch.object(native_topic_text, "split_resolved_topic_title", split),
        ):
            with (
                patch.object(message, "MAX_TOPIC_NAME_LENGTH", 3),
                patch.object(message, "TOPIC_TRUNCATION_MESSAGE", ShortSuffix("...")),
            ):
                self.assertEqual(message.truncate_topic("abcdef"), "ab...")
            with (
                patch.object(message, "MAX_TOPIC_NAME_LENGTH", 5),
                patch.object(message, "TOPIC_TRUNCATION_MESSAGE", "+"),
            ):
                self.assertEqual(message.truncate_topic("abcdef"), "abcd+")
            with (
                patch.object(message, "MAX_TOPIC_NAME_LENGTH", 2.0),
                self.assertRaisesRegex(TypeError, "slice indices must be integers"),
            ):
                message.truncate_topic("abcdef")

            with patch.object(topic, "RESOLVED_TOPIC_PREFIX", "RESOLVED "):
                self.assertEqual(
                    topic.get_topic_resolution_and_bare_name("RESOLVED synthetic"),
                    (True, "synthetic"),
                )
            with (
                patch.object(topic, "RESOLVED_TOPIC_PREFIX", ("✔ ",)),
                self.assertRaises(TypeError),
            ):
                topic.get_topic_resolution_and_bare_name("✔ synthetic")
        truncate.assert_not_called()
        split.assert_not_called()

    def test_native_decline_preserves_subclass_surrogates_and_legacy_errors(self) -> None:
        class LegacyTitle(str):
            __slots__ = ()

            @override
            def __len__(self) -> int:
                return 1

            @override
            def removeprefix(self, prefix: str, /) -> str:
                return "Python subclass"

        unusual = LegacyTitle("✔ legacy")
        surrogate = "✔ \ud800"
        native = extension()
        for truncate, split in (
            (None, None),
            (native.truncate_topic_title, native.split_resolved_topic_title),
        ):
            with (
                patch.object(native_topic_text, "truncate_topic_title", truncate),
                patch.object(native_topic_text, "split_resolved_topic_title", split),
                patch.object(message, "MAX_TOPIC_NAME_LENGTH", 3),
            ):
                self.assertIs(message.truncate_topic(unusual), unusual)
                self.assertIs(message.truncate_topic(surrogate), surrogate)
                self.assertEqual(
                    topic.get_topic_resolution_and_bare_name(unusual), (True, "Python subclass")
                )
                self.assertEqual(
                    topic.get_topic_resolution_and_bare_name(surrogate), (True, "\ud800")
                )
                malformed = cast("str", None)
                with self.assertRaisesRegex(TypeError, "has no len"):
                    message.truncate_topic(malformed)
                with self.assertRaisesRegex(AttributeError, "has no attribute 'startswith'"):
                    topic.get_topic_resolution_and_bare_name(malformed)
