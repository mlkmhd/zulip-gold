from importlib import import_module
from os import environ
from types import ModuleType, SimpleNamespace
from typing import cast
from unittest import SkipTest, TestCase
from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase

from zerver.lib import message, reminders
from zerver.lib.mention import silent_mention_syntax_for_user
from zerver.lib.message_cache import MessageDict
from zerver.lib.native_backend import supports_default_rust_backend
from zerver.lib.types import UserDisplayRecipient
from zerver.models import Message, Realm, Recipient, UserProfile


def extension() -> ModuleType:
    try:
        native = import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(native, "plan_display_mentions", None)):
        raise SkipTest("Rust extension does not support display mention planning")
    return native


class DisplayMentionsRustTest(TestCase):
    def test_presentation_plan(self) -> None:
        plan = extension().plan_display_mentions

        encoded = b"@_**synthetic**"
        one = encoded.decode()
        self.assertIs(plan([one], True)[1], one)
        self.assertEqual(plan([one], False), (None, one))
        equal_one = encoded.decode()
        self.assertIsNot(one, equal_one)
        self.assertIs(plan([one, equal_one], True)[1], equal_one)
        a = "@_**e\u0301**"
        z = "@_**é**"
        astral = "@_**😀**"
        values = [astral, z, a, z]
        self.assertEqual(plan(values, True), (f"{a}, {z}, {z},", astral))
        self.assertEqual(plan(values, False), (f"{astral}, {z}, {a},", z))
        self.assertEqual(values, [astral, z, a, z])
        self.assertEqual(plan(["", ""], False), ("", ""))
        self.assertEqual(plan(["", "", ""], False), (", ,", ""))

        class Derived(str):
            __slots__ = ()

        for invalid in ([], (one,), [one, 5], [one, Derived("plain")]):
            self.assertIsNone(plan(invalid, False))
        self.assertIsNone(plan([one], 1))

    def test_joins_and_sorts_all_surrogate_code_points(self) -> None:
        native = extension()
        if getattr(native, "DISPLAY_MENTION_API", None) != 1:
            self.skipTest("Wheel predates the surrogate-safe display mention contract")
        for point in range(0xD800, 0xE000):
            item = chr(point)
            values = ["\uf000", item, "😀", "\ud7ff", "\ud800\udc00"]
            self.assertIs(native.plan_display_mentions([item], True)[1], item)
            for sort in (False, True):
                expected = sorted(values) if sort else values
                actual = native.plan_display_mentions(values, sort)
                self.assertIsNotNone(actual)
                assert actual is not None
                self.assertEqual(actual, (", ".join(expected[:-1]) + ",", expected[-1]))
                self.assertIs(actual[1], expected[-1])


class DisplayMentionsDjangoDispatchRustTest(SimpleTestCase):
    def test_sorted_builder_keeps_exact_locale_and_builds_each_mention_once(self) -> None:
        native = extension()
        enabled = environ.get("ZULIP_RUST_DISPLAY_MENTIONS") == "1" or (
            supports_default_rust_backend() and getattr(native, "DISPLAY_MENTION_API", None) == 1
        )
        self.assertIs(message._rust_display_mentions, native if enabled else None)
        users: list[UserProfile | UserDisplayRecipient] = [
            UserProfile(id=30, full_name="😀"),
            UserProfile(id=20, full_name="é"),
            UserProfile(id=10, full_name="e\u0301"),
        ]
        strings = ["@_**😀|30**", "@_**é|20**", "@_**e\u0301|10**"]
        with (
            patch.object(
                native, "plan_display_mentions", wraps=native.plan_display_mentions
            ) as plan,
            patch.object(
                message,
                "silent_mention_syntax_for_user",
                wraps=silent_mention_syntax_for_user,
            ) as create,
            patch.object(message, "_", return_value="〈{last_user}〉 {other_users}") as translate,
        ):
            self.assertEqual(
                message.get_user_mentions_for_display(users),
                "〈@_**😀|30**〉 @_**e\u0301|10**, @_**é|20**,",
            )
        self.assertEqual(create.call_args_list, [call(user) for user in users])
        translate.assert_called_once_with("{other_users} and {last_user}")
        if enabled:
            plan.assert_called_once_with(strings, True)
        else:
            plan.assert_not_called()

    def test_read_only_reminder_formatting_uses_presorted_strings(self) -> None:
        native = extension()
        enabled = environ.get("ZULIP_RUST_DISPLAY_MENTIONS") == "1" or (
            supports_default_rust_backend() and getattr(native, "DISPLAY_MENTION_API", None) == 1
        )
        realm = Realm(string_id="synthetic")
        sender = UserProfile(realm=realm, id=60, full_name="Synthetic author")
        viewer = UserProfile(realm=realm, id=61, full_name="Synthetic viewer")
        recipient = Recipient(id=5, type=Recipient.DIRECT_MESSAGE_GROUP, type_id=1)
        original = Message(
            id=22,
            realm=realm,
            sender=sender,
            recipient=recipient,
            content="Synthetic body",
            is_channel_message=False,
        )
        display = [
            UserDisplayRecipient(
                id=identifier,
                full_name=name,
                email="synthetic@example.invalid",
                is_mirror_dummy=False,
            )
            for identifier, name in ((60, "Synthetic author"), (63, "Ω"), (62, "A"))
        ]
        mentions = ["@_**A|62**", "@_**Ω|63**"]

        with (
            patch.object(reminders, "get_display_recipient", return_value=display),
            patch.object(MessageDict, "wide_dict", return_value={}),
            patch.object(reminders, "message_link_url", return_value="#narrow/dm/60,62,63/near/22"),
            patch.object(
                reminders, "send_event_on_commit", side_effect=AssertionError("no event delivery")
            ),
            patch.object(reminders, "_rust_reminder_context", None),
            patch.object(
                native, "plan_display_mentions", wraps=native.plan_display_mentions
            ) as plan,
        ):
            preview = reminders.get_reminder_formatted_content(original, viewer)
        self.assertIn("@_**Synthetic author|60** [said](#narrow/dm/60,62,63/near/22)", preview)
        self.assertIn("to @_**A|62** and @_**Ω|63**:", preview)
        self.assertIn("\n```quote\nSynthetic body\n```", preview)
        if enabled:
            plan.assert_called_once_with(mentions, False)
        else:
            plan.assert_not_called()

    def test_old_wheel_and_decline_keep_order_without_rebuilding_mentions(self) -> None:
        users: list[UserProfile | UserDisplayRecipient] = [
            UserProfile(id=1, full_name="Synthetic first"),
            UserProfile(id=2, full_name="Synthetic second"),
        ]
        decline = Mock(return_value=None)
        for backend in (None, SimpleNamespace(), SimpleNamespace(plan_display_mentions=decline)):
            with (
                patch.object(message, "_rust_display_mentions", backend),
                patch.object(
                    message, "silent_mention_syntax_for_user", side_effect=["z", "a"]
                ) as make,
            ):
                self.assertEqual(message.get_user_mentions_for_display(users), "a and z")
            self.assertEqual(make.call_args_list, [call(user) for user in users])
        decline.assert_called_once_with(["z", "a"], True)

    def test_native_decline_and_empty_head_preserve_identity_and_errors(self) -> None:
        native = extension()
        single = b"@_**one**".decode()
        object_single = object()
        for backend in (None, native):
            with (
                patch.object(message, "_rust_display_mentions", backend),
                patch.object(message, "_", return_value="{last_user}|{other_users}") as translate,
            ):
                self.assertIs(message.format_sorted_user_mentions([single]), single)
                self.assertIs(
                    message.format_sorted_user_mentions(cast("list[str]", [object_single])),
                    object_single,
                )
                self.assertEqual(message.format_sorted_user_mentions(["", ""]), "|")
                self.assertEqual(message.format_sorted_user_mentions(["z", "\ud800"]), "\ud800|z")
                with self.assertRaisesRegex(IndexError, "list index out of range"):
                    message.format_sorted_user_mentions([])
                with self.assertRaisesRegex(IndexError, "list index out of range"):
                    message.get_user_mentions_for_display([])
            self.assertEqual(translate.call_args_list, [call("{other_users} and {last_user}")] * 2)
