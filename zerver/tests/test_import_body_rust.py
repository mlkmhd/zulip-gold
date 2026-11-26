from importlib import import_module
from os import environ
from random import Random
from types import ModuleType, SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from zerver.data_import.import_util import ZerverFieldsT, build_message
from zerver.lib import message
from zerver.lib.topic import EXPORT_TOPIC_NAME
from zerver.models import Message


def extension() -> ModuleType:
    try:
        native = import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(native, "normalize_imported_message_body", None)):
        raise SkipTest("Rust extension does not support import body normalization")
    return native


class ImportBodyRustTest(SimpleTestCase):
    def test_preserves_whitespace_and_removes_nul_before_cutoff(self) -> None:
        native = extension()
        self.assertEqual(native.normalize_imported_message_body("\n\0 hi \0\t\n", 7), "\n hi \t\n")
        self.assertEqual(native.normalize_imported_message_body("\0\0", 0), "")
        self.assertEqual(native.normalize_imported_message_body("\n\n", 2), "\n\n")
        self.assertEqual(native.normalize_imported_message_body("\0", -1), "\n[message truncated]")

    def test_signed_python_slice_and_codepoint_length(self) -> None:
        native = extension()
        suffix = "\n[message truncated]"
        rng = Random(44416)
        parts = ("", "\0", "🙂", "فارسی\n", "e\u0301", "\u200d", "\r\n", " ")
        for limit in [-(2**63), -21, -1, 0, 2, 17, 19, 20, 21, 23, 200, 2**63 - 1]:
            for length in range(20):
                body = "".join(rng.choices(parts, k=length))
                text = body.replace("\0", "")
                expected = text[: limit - len(suffix)] + suffix if len(text) > limit else text
                self.assertEqual(native.normalize_imported_message_body(body, limit), expected)
        self.assertEqual(
            native.normalize_imported_message_body("🙂" * 40, 19),
            "🙂" * 39 + suffix,
        )

    def test_old_inputs_can_fall_back_to_python(self) -> None:
        class BodySubclass(str):
            __slots__ = ()

        native = extension()
        for body in ("\ud800", None, b"a", BodySubclass("a")):
            self.assertIsNone(native.normalize_imported_message_body(body, 200))
        for limit in (True, 2**100, 20.0, "20"):
            self.assertIsNone(native.normalize_imported_message_body("scratch", limit))


@override_settings(MAX_MESSAGE_LENGTH=21)
class ImportBodyDjangoDispatchRustTest(SimpleTestCase):
    @staticmethod
    def build(content: str, *, direct: bool = False) -> ZerverFieldsT:
        return build_message(
            topic_name="  Synthetic topic  ",
            date_sent=1.0,
            message_id=2,
            content=content,
            rendered_content="<p>unchanged</p>",
            user_id=3,
            recipient_id=4,
            realm_id=5,
            is_channel_message=not direct,
            is_direct_message_type=direct,
        )

    def test_builder_uses_current_extension_only_in_enabled_process(self) -> None:
        native = extension()
        body = "\0 🙂\0e\u0301" + "🙂" * 20 + "\n"
        with (
            patch.object(message, "_rust_exact_import_body", None),
            patch.object(
                native,
                "normalize_imported_message_body",
                wraps=native.normalize_imported_message_body,
            ) as call,
        ):
            record = self.build(body)
        self.assertEqual(record["content"], " \n[message truncated]")
        self.assertEqual(record["rendered_content"], "<p>unchanged</p>")
        self.assertEqual(record[EXPORT_TOPIC_NAME], "  Synthetic topic  ")
        self.assertEqual(record["sender"], 3)
        self.assertEqual(record["recipient"], 4)
        if environ.get("ZULIP_RUST_IMPORT_BODY") == "1":
            call.assert_called_once_with(body, 21)
        else:
            call.assert_not_called()

    def test_builder_keeps_even_an_empty_native_result(self) -> None:
        normalize = Mock(return_value="")
        backend = SimpleNamespace(normalize_imported_message_body=normalize)
        with (
            patch.object(message, "_rust_exact_import_body", None),
            patch.object(message, "_rust_import_body", backend),
        ):
            self.assertEqual(self.build("synthetic")["content"], "")
        normalize.assert_called_once_with("synthetic", 21)

    def test_builder_falls_back_for_missing_old_and_declining_extension(self) -> None:
        body = "\n\0 hello \t"
        decline = Mock(return_value=None)
        for backend in (
            None,
            SimpleNamespace(),
            SimpleNamespace(normalize_imported_message_body=decline),
        ):
            with (
                patch.object(message, "_rust_exact_import_body", None),
                patch.object(message, "_rust_import_body", backend),
            ):
                record = self.build(body, direct=True)
            self.assertEqual(record["content"], "\n hello \t")
            self.assertEqual(record[EXPORT_TOPIC_NAME], Message.DM_TOPIC)
            self.assertEqual(record["rendered_content"], "<p>unchanged</p>")
        decline.assert_called_once_with(body, 21)

    def test_builder_keeps_legacy_values_and_errors_on_native_decline(self) -> None:
        native = extension()
        with (
            patch.object(message, "_rust_import_body", native),
            override_settings(MAX_MESSAGE_LENGTH=2**100),
        ):
            self.assertEqual(self.build("\ud800\0 ")["content"], "\ud800 ")

        errors = []
        # A rejected, malformed length must keep the historical Python error.
        for backend in (None, native):
            with (
                patch.object(message, "_rust_import_body", backend),
                override_settings(MAX_MESSAGE_LENGTH="invalid"),
                self.assertRaises(TypeError) as error,
            ):
                self.build("synthetic")
            errors.append(str(error.exception))
        self.assertEqual(errors, ["'>' not supported between instances of 'int' and 'str'"] * 2)

    def test_exact_matching_wheel_keeps_unicode_and_whitespace_in_import_record(self) -> None:
        backend = message._rust_exact_import_body
        if getattr(backend, "IMPORT_BODY_TEXT_API", None) != 1:
            self.skipTest("Matching Rust import body contract is not available")

        body = " \ud800\0" + "🙂" * 23
        with patch.object(
            backend,
            "normalize_exact_imported_message_body",
            wraps=backend.normalize_exact_imported_message_body,
        ) as normalize:
            record = self.build(body, direct=True)
        normalize.assert_called_once_with(body, 21)
        self.assertEqual(record["content"], " \n[message truncated]")
        self.assertEqual(record[EXPORT_TOPIC_NAME], Message.DM_TOPIC)
        self.assertEqual(record["rendered_content"], "<p>unchanged</p>")
        existing = " \ud800🙂"
        self.assertIs(message.normalize_body_for_import(existing), existing)
        self.assertEqual(message.normalize_body_for_import("\0\ud800🙂"), "\ud800🙂")

    def test_custom_truncation_still_uses_python(self) -> None:
        body = "x\0y"
        with (
            patch.object(message, "_rust_import_body", None),
            patch.object(message, "truncate_content", return_value="custom") as truncate,
        ):
            self.assertEqual(self.build(body)["content"], "custom")
        truncate.assert_called_once_with("xy", 21, "\n[message truncated]")
