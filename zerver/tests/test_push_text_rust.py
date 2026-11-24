from importlib import import_module
from os import environ
from random import Random
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson
from django.test import SimpleTestCase

from zerver.lib import native_push_text
from zerver.lib.push_notifications import (
    get_message_payload,
    get_message_payload_apns,
    get_message_payload_gcm,
    get_mobile_push_content,
    truncate_content,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message
from zerver.models.scheduled_jobs import NotificationTriggers
from zerver.models.streams import get_stream


def extension() -> ModuleType:
    try:
        native = import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        raise SkipTest("Rust extension is not installed") from None
    for name in ("quote_mobile_push_text", "truncate_mobile_push_text"):
        if not callable(getattr(native, name, None)):
            raise SkipTest("Rust extension does not support mobile push text")
    return native


class PushTextRustTest(SimpleTestCase):
    def test_quotes_splitlines_without_discarding_whitespace(self) -> None:
        native = extension()
        boundaries = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
        for boundary in [*boundaries, "\r\n"]:
            with self.subTest(boundary=boundary):
                text = boundary.join(("", "سلام", "", "  ", "🙂", ""))
                self.assertEqual(native.quote_mobile_push_text(text), "> سلام\n>   \n> 🙂\n")
        self.assertEqual(native.quote_mobile_push_text(boundaries), "")
        self.assertEqual(native.quote_mobile_push_text("> old\r\n\t"), "> > old\n> \t\n")

    def test_truncation_uses_exact_legacy_codepoint_boundary(self) -> None:
        native = extension()
        rng = Random(49381)
        for length in [0, 1, 198, 199, 200, 201, 202, 600]:
            for _ in range(20):
                text = "".join(rng.choice("aé\u0301🙂\u200d\ufe0fم\r\n") for _ in range(length))
                expected = (text[:200] + "…", True) if length > 200 else (text, False)
                self.assertEqual(native.truncate_mobile_push_text(text), expected)
        text = "🙂" * 200
        result = native.truncate_mobile_push_text(text)
        self.assertIs(result[0], text)
        self.assertEqual(result, (text, False))
        self.assertEqual(native.truncate_mobile_push_text(text + "a"), (text + "…", True))

    def test_noncanonical_strings_decline_to_python(self) -> None:
        class StringSubclass(str):
            __slots__ = ()

        native = extension()
        for value in ("\ud800", None, b"plain bytes", StringSubclass("plain subclass")):
            self.assertIsNone(native.quote_mobile_push_text(value))
            self.assertIsNone(native.truncate_mobile_push_text(value))

    def test_noncanonical_plaintext_uses_existing_django_cutoff(self) -> None:
        class StringSubclass(str):
            __slots__ = ()

        exact_limit = StringSubclass("🙂" * 200)
        surrogate = "a" * 199 + "\ud800" + "x"
        with patch.object(native_push_text, "_backend", extension()):
            self.assertIsNone(native_push_text.truncate(exact_limit))
            self.assertIsNone(native_push_text.quote(surrogate))
            unchanged, truncated = truncate_content(exact_limit)
            self.assertIs(unchanged, exact_limit)
            self.assertFalse(truncated)
            self.assertEqual(truncate_content(surrogate), (surrogate[:200] + "…", True))

    def test_existing_quote_helper_keeps_whitespace_and_rolls_back(self) -> None:
        html = "<blockquote><p>سلام</p>\n<p>🙂</p></blockquote>"
        with patch.object(native_push_text, "_backend", None):
            expected = get_mobile_push_content(html)
        self.assertEqual(expected, "> سلام\n> 🙂\n")
        for candidate in (
            SimpleNamespace(),
            SimpleNamespace(quote_mobile_push_text=Mock(return_value=None)),
        ):
            with patch.object(native_push_text, "_backend", candidate):
                self.assertEqual(get_mobile_push_content(html), expected)


class PushTextPayloadRustTest(ZulipTestCase):
    def test_synthetic_apns_and_gcm_payload_bytes_across_modes(self) -> None:
        backend = extension()
        enabled = environ.get("ZULIP_RUST_PUSH_TEXT") == "1"
        actor = self.example_user("hamlet")
        actor.default_language = "fa"
        stream = get_stream("Verona", actor.realm)
        assert stream.recipient_id is not None
        message = (
            Message.objects.filter(realm_id=actor.realm_id, recipient_id=stream.recipient_id)
            .order_by("id")
            .first()
        )
        assert message is not None
        body = "🙂" * 197 + "e\u0301x"
        message.rendered_content = f"<blockquote><p>{body}</p></blockquote>"
        quote = f"> {body}\n"
        expected_text = quote[:200] + "…"

        def payloads() -> tuple[dict[str, Any], tuple[dict[str, Any], dict[str, Any]]]:
            shared = get_message_payload(actor, message)
            apns = get_message_payload_apns(shared, actor, message, NotificationTriggers.MENTION)
            gcm = get_message_payload_gcm(shared, actor, message)
            return apns, gcm

        with patch.object(native_push_text, "_backend", None):
            expected = payloads()
        self.assertEqual(expected[0]["alert"]["body"], expected_text)
        self.assertEqual(expected[1][0]["content"], expected_text)
        self.assertTrue(expected_text.endswith("e…"))
        expected_bytes = orjson.dumps(expected)

        quote_native = backend.quote_mobile_push_text
        truncate_native = backend.truncate_mobile_push_text
        quotes: list[str | None] = []
        cuts: list[tuple[str, bool] | None] = []

        def witness_quote(text: str) -> str | None:
            self.assertEqual(text, body)
            result: str | None = quote_native(text)
            quotes.append(result)
            return result

        def witness_truncation(text: str) -> tuple[str, bool] | None:
            self.assertEqual(text, quote)
            result: tuple[str, bool] | None = truncate_native(text)
            cuts.append(result)
            return result

        with (
            patch.object(backend, "quote_mobile_push_text", side_effect=witness_quote),
            patch.object(backend, "truncate_mobile_push_text", side_effect=witness_truncation),
        ):
            self.assertEqual(orjson.dumps(payloads()), expected_bytes)
        self.assertEqual(quotes, [quote, quote] if enabled else [])
        self.assertEqual(cuts, [(expected_text, True)] * 2 if enabled else [])

        reject_quote = Mock(return_value=None)
        reject_truncate = Mock(return_value=None)
        for candidate in (
            SimpleNamespace(),
            SimpleNamespace(
                quote_mobile_push_text=reject_quote, truncate_mobile_push_text=truncate_native
            ),
            SimpleNamespace(
                quote_mobile_push_text=quote_native, truncate_mobile_push_text=reject_truncate
            ),
        ):
            with patch.object(native_push_text, "_backend", candidate):
                self.assertEqual(orjson.dumps(payloads()), expected_bytes)
        self.assertEqual(reject_quote.call_count, 2)
        self.assertEqual(reject_truncate.call_count, 2)
