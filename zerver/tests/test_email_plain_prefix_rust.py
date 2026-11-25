from datetime import datetime, timezone
from importlib import import_module
from os import environ
from random import Random
from types import ModuleType, SimpleNamespace
from typing import SupportsIndex, cast
from unittest import SkipTest
from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase
from markupsafe import escape
from typing_extensions import override

from zerver.lib import email_notifications
from zerver.lib.email_notifications import MessageListPayload, build_message_list
from zerver.lib.native_backend import supports_default_rust_backend
from zerver.models import Message, Realm, Recipient, UserProfile


def extension() -> ModuleType:
    try:
        native = import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        raise SkipTest("Rust extension is not installed") from None
    for name in ("quote_prepared_email_plaintext", "prepend_prepared_email_plaintext_sender"):
        if not callable(getattr(native, name, None)):
            raise SkipTest("Rust extension does not support plain email decoration")
    return native


class EmailPlainPrefixRustTest(SimpleTestCase):
    def test_literal_line_boundaries_and_sender_prefix(self) -> None:
        native = extension()
        for plain, quoted in [
            ("", "> "),
            ("\n", "> \n> "),
            ("a\r\n\n", "> a\r\n> \n> "),
            ("آزمایش\u2028🙂\r\u0085\0", "> آزمایش\u2028🙂\r\u0085\0"),
            ("> quoting\n  ", "> > quoting\n>   "),
        ]:
            self.assertEqual(native.quote_prepared_email_plaintext(plain), quoted)
        self.assertEqual(
            native.prepend_prepared_email_plaintext_sender("> first\n> ", "آزمایش 🙂"),
            "آزمایش 🙂:\n> first\n> ",
        )
        self.assertEqual(native.prepend_prepared_email_plaintext_sender("", ""), ":\n")

    def test_matches_composed_reference_and_declines_exotic_objects(self) -> None:
        native = extension()
        rng = Random(32412)
        parts = ("", "\n", "\r", "\r\n", "\t", "\u2028", "\u0085", "\0", "🙂", "آزمایش")
        for _ in range(500):
            plain = "".join(rng.choices(parts, k=rng.randrange(40)))
            sender = "".join(rng.choices(parts, k=rng.randrange(8)))
            quoted = "\n".join(f"> {line}" for line in plain.split("\n"))
            self.assertEqual(native.quote_prepared_email_plaintext(plain), quoted)
            self.assertEqual(
                native.prepend_prepared_email_plaintext_sender(quoted, sender),
                f"{sender}:\n{quoted}",
            )

        class TextSubclass(str):
            __slots__ = ()

        for unusual in ("\ud800", None, b"text", TextSubclass("synthetic")):
            self.assertIsNone(native.quote_prepared_email_plaintext(unusual))
            self.assertIsNone(native.prepend_prepared_email_plaintext_sender(unusual, "A"))
            self.assertIsNone(native.prepend_prepared_email_plaintext_sender("A", unusual))


class EmailPlainPrefixDjangoDispatchRustTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.realm = Realm(id=8, string_id="synthetic")
        self.reader = UserProfile(id=2, realm=self.realm, default_language="en")
        self.sender = UserProfile(id=3, realm=self.realm, full_name="فرستنده <&>")
        self.other_sender = UserProfile(id=4, realm=self.realm, full_name="Second sender")
        self.recipient = Recipient(id=5, type=Recipient.STREAM, type_id=6)
        for delivery in ("send_future_email", "queue_event_on_commit"):
            delivery_patch = patch.object(
                email_notifications,
                delivery,
                side_effect=AssertionError("rendering must not send or queue email"),
            )
            delivery_patch.start()
            self.addCleanup(delivery_patch.stop)

    def fixture_message(
        self, sender: UserProfile, plain: str, html: str, minute: int = 0
    ) -> Message:
        result = Message(
            sender=sender,
            realm=self.realm,
            recipient=self.recipient,
            content=plain,
            rendered_content=html,
            date_sent=datetime(2026, 1, 1, 0, minute, tzinfo=timezone.utc),
        )
        result.set_topic_name("synthetic title")
        return result

    def render_one(self, plain: str = "a\r\n") -> MessageListPayload:
        return build_message_list(
            self.reader, [self.fixture_message(self.sender, plain, "<p>synthetic body</p>")]
        )

    def test_real_builder_only_passes_processed_plaintext_and_keeps_html(self) -> None:
        native = extension()
        enabled = environ.get("ZULIP_RUST_EMAIL_PLAIN_PREFIX") == "1" or (
            supports_default_rust_backend() and getattr(native, "EMAIL_PLAIN_PREFIX_API", None) == 1
        )
        self.assertIs(email_notifications._rust_email_plain_prefix, native if enabled else None)
        opener = "```spoiler title"
        closure = "```"
        plaintext = (
            f"آزمایش 🙂\r\n[proof](/user_uploads/sample)\n{opener}\nprivate fixture\n{closure}\n"
        )
        html = (
            '<p>آزمایش 🙂 <a href="/user_uploads/sample">proof</a></p>'
            '<div class="spoiler-block"><div class="spoiler-header"><p>title</p></div>'
            '<div class="spoiler-content" aria-hidden="true"><p>private fixture</p></div></div>'
        )
        first = self.fixture_message(self.sender, plaintext, html)
        same_sender = self.fixture_message(self.sender, "بعدی\r\n", "<p>follow-up</p>", 1)
        other_sender = self.fixture_message(self.other_sender, "", "<p></p>", 2)

        def render() -> MessageListPayload:
            return build_message_list(self.reader, [other_sender, same_sender, first])

        with patch.object(email_notifications, "_rust_email_plain_prefix", None):
            expected = render()
        with (
            patch.object(
                native,
                "quote_prepared_email_plaintext",
                wraps=native.quote_prepared_email_plaintext,
            ) as quote,
            patch.object(
                native,
                "prepend_prepared_email_plaintext_sender",
                wraps=native.prepend_prepared_email_plaintext_sender,
            ) as prepend,
        ):
            actual = render()

        translated_concealment = "(Open Zulip to see the spoiler content)"
        rewritten_url = self.realm.url + "/user_uploads/sample"
        processed = f"آزمایش 🙂\r\n{rewritten_url}\n{opener}\n{translated_concealment}\n{closure}\n"
        quoted = "\n".join(f"> {line}" for line in processed.split("\n"))
        self.assertEqual(actual, expected)
        self.assertEqual(
            [sender.sender for sender in actual.senders],
            [self.sender.full_name, self.other_sender.full_name],
        )
        first_result = actual.senders[0].content[0]
        self.assertEqual(first_result.plain, f"{self.sender.full_name}:\n{quoted}")
        self.assertEqual(actual.senders[0].content[1].plain, "> بعدی\r\n> ")
        self.assertEqual(actual.senders[1].content[0].plain, "Second sender:\n> ")
        self.assertIn(f"<b>{escape(self.sender.full_name)}</b>:", first_result.html)
        self.assertIn(rewritten_url, first_result.html)
        self.assertIn("spoiler-title", first_result.html)
        self.assertNotIn("private fixture", first_result.plain)
        self.assertNotIn("private fixture", first_result.html)
        if enabled:
            self.assertEqual(quote.call_args_list, [call(processed), call("بعدی\r\n"), call("")])
            self.assertEqual(
                prepend.call_args_list,
                [call(quoted, self.sender.full_name), call("> ", self.other_sender.full_name)],
            )
        else:
            quote.assert_not_called()
            prepend.assert_not_called()

    def test_missing_old_and_declining_helpers_fall_back_independently(self) -> None:
        with patch.object(email_notifications, "_rust_email_plain_prefix", None):
            expected = self.render_one()
        quote = Mock(return_value="")
        prepend = Mock(return_value="")
        decline_quote = Mock(return_value=None)
        decline_prepend = Mock(return_value=None)
        for backend, expected_plain in (
            (SimpleNamespace(), expected.senders[0].content[0].plain),
            (
                SimpleNamespace(
                    quote_prepared_email_plaintext=decline_quote,
                    prepend_prepared_email_plaintext_sender=decline_prepend,
                ),
                expected.senders[0].content[0].plain,
            ),
            (
                SimpleNamespace(quote_prepared_email_plaintext=quote),
                f"{self.sender.full_name}:\n",
            ),
            (SimpleNamespace(prepend_prepared_email_plaintext_sender=prepend), ""),
        ):
            with patch.object(email_notifications, "_rust_email_plain_prefix", backend):
                actual = self.render_one()
            self.assertEqual(actual.senders[0].content[0].plain, expected_plain)
            self.assertEqual(actual.senders[0].content[0].html, expected.senders[0].content[0].html)
        quote.assert_called_once_with("a\r\n")
        prepend.assert_called_once_with("> a\r\n> ", self.sender.full_name)
        decline_quote.assert_called_once_with("a\r\n")
        decline_prepend.assert_called_once_with("> a\r\n> ", self.sender.full_name)

    def test_native_decline_keeps_real_subclass_formatting_and_legacy_errors(self) -> None:
        class SplitText(str):
            __slots__ = ()

            @override
            def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
                return ["historical split"]

        class FormattedSender(str):
            __slots__ = ()

            @override
            def __format__(self, format_spec: str, /) -> str:
                return "historical formatter"

        self.sender.full_name = FormattedSender("Original name")
        native = extension()
        snapshots = []
        for backend in (None, native):
            with patch.object(email_notifications, "_rust_email_plain_prefix", backend):
                self.assertEqual(
                    email_notifications.add_quote_prefix_in_text(SplitText("source")),
                    "> historical split",
                )
                self.assertEqual(email_notifications.add_quote_prefix_in_text("\ud800"), "> \ud800")
                with self.assertRaisesRegex(AttributeError, "has no attribute 'split'"):
                    email_notifications.add_quote_prefix_in_text(cast("str", None))
                snapshots.append(self.render_one())
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(
            snapshots[1].senders[0].content[0].plain,
            "historical formatter:\n> a\r\n> ",
        )
