from types import ModuleType
from unittest.mock import patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import native_push_text, push_notifications
from zerver.lib.push_notifications import get_apns_alert_title, get_message_payload_apns
from zerver.models import Message, Recipient, UserProfile


class APNSAlertTitleRustTest(SimpleTestCase):
    def test_group_unicode_order_and_small_direct_message(self) -> None:
        user = UserProfile(full_name="Synthetic Sender")
        message = Message(sender=user, recipient=Recipient(type=Recipient.DIRECT_MESSAGE_GROUP))
        names = ["آرش", "🙂", "\ud800", "A", "a", "آرش"]
        display = [{"full_name": name} for name in names]
        for entries, expected in (
            (display, ", ".join(sorted(names))),
            (display[:2], user.full_name),
        ):
            with patch.object(
                push_notifications, "get_display_recipient", return_value=entries
            ) as get:
                self.assertEqual(get_apns_alert_title(message), expected)
            get.assert_called_once_with(message.recipient)

    def test_channel_preserves_empty_topic_and_string_subclass_formatting(self) -> None:
        class TitleText(str):
            __slots__ = ()

            @override
            def __format__(self, format_spec: str) -> str:
                return "formatted title"

        message = Message(recipient=Recipient(type=Recipient.STREAM))
        self.assertEqual(get_apns_alert_title(message, "کانال \udfff", ""), "#کانال \udfff > ")
        self.assertEqual(
            get_apns_alert_title(message, TitleText("raw"), "Topic"), "#formatted title > Topic"
        )
        with self.assertRaises(AssertionError):
            get_apns_alert_title(message)

    def test_actual_payload_keeps_title_subtitle_and_body(self) -> None:
        user = UserProfile(full_name="Synthetic Sender", default_language="en")
        message = Message(
            id=305,
            recipient=Recipient(type=Recipient.STREAM),
            sender=user,
            rendered_content="<p>Synthetic message</p>",
        )
        payload = {"server": "synthetic.invalid", "realm_id": 1, "stream": "مثال", "topic": "آغاز"}
        with patch.object(push_notifications, "get_apns_badge_count", return_value=3):
            with patch.object(native_push_text, "_apns_alert_title_backend", ModuleType("old")):
                reference = get_message_payload_apns(payload, user, message, "ordinary")
            actual = get_message_payload_apns(payload, user, message, "ordinary")
        self.assertEqual(actual, reference)
        self.assertEqual(actual["alert"]["title"], "#مثال > آغاز")
        self.assertEqual(actual["alert"]["subtitle"], "Synthetic Sender:")
        self.assertEqual(actual["alert"]["body"], "Synthetic message")
        self.assertEqual(actual["badge"], 3)

    def test_current_native_is_called_for_prepared_channel(self) -> None:
        native = native_push_text._apns_alert_title_backend
        if native is None:
            self.skipTest("requires the current native APNS title contract")
        message = Message(recipient=Recipient(type=Recipient.STREAM))
        with patch.object(
            native, "format_apns_channel_title", wraps=native.format_apns_channel_title
        ) as call:
            self.assertEqual(get_apns_alert_title(message, "C", "T"), "#C > T")
        call.assert_called_once_with("C", "T")
