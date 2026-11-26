from types import ModuleType
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils.translation import override as override_language

from zerver.lib import native_push_text, push_notifications
from zerver.lib.push_notifications import get_apns_alert_subtitle, get_message_payload_apns
from zerver.models import Message, Recipient, UserProfile
from zerver.models.scheduled_jobs import NotificationTriggers


class APNSAlertSubtitleRustTest(SimpleTestCase):
    def test_all_mentions_use_translation_without_loading_recipients(self) -> None:
        user = UserProfile(full_name="Sender")
        message = Message(sender=user, recipient=Recipient(type=Recipient.DIRECT_MESSAGE_GROUP))
        cases = [
            (NotificationTriggers.MENTION, "", "Sender mentioned @:"),
            (NotificationTriggers.MENTION, "گروه", "Sender mentioned @گروه:"),
            (NotificationTriggers.MENTION, None, "Sender mentioned you:"),
            (
                NotificationTriggers.TOPIC_WILDCARD_MENTION_IN_FOLLOWED_TOPIC,
                None,
                "Sender mentioned everyone:",
            ),
            (
                NotificationTriggers.STREAM_WILDCARD_MENTION_IN_FOLLOWED_TOPIC,
                None,
                "Sender mentioned everyone:",
            ),
            (NotificationTriggers.TOPIC_WILDCARD_MENTION, None, "Sender mentioned everyone:"),
            (NotificationTriggers.STREAM_WILDCARD_MENTION, None, "Sender mentioned everyone:"),
        ]
        with (
            override_language("en"),
            patch.object(
                push_notifications,
                "get_display_recipient",
                side_effect=AssertionError("no lookup for mentions"),
            ) as load,
        ):
            for trigger, group, expected in cases:
                self.assertEqual(get_apns_alert_subtitle(message, trigger, group), expected)
        load.assert_not_called()

    def test_only_ordinary_group_loads_recipients_once_and_preserves_surrogates(self) -> None:
        user = UserProfile(full_name="نام \ud800")
        message = Message(sender=user, recipient=Recipient(type=Recipient.DIRECT_MESSAGE_GROUP))
        for count, expected in ((2, ""), (3, "نام \ud800:")):
            with patch.object(
                push_notifications, "get_display_recipient", return_value=[{}] * count
            ) as load:
                self.assertEqual(get_apns_alert_subtitle(message, "ordinary"), expected)
            load.assert_called_once_with(message.recipient)

    def test_actual_apns_payload_preserves_hidden_label_and_bytes(self) -> None:
        sender = UserProfile(full_name="Hidden Real Name")
        message = Message(
            id=120,
            sender=sender,
            recipient=Recipient(type=Recipient.STREAM),
            rendered_content="<p>Text 🙂</p>",
        )
        shared = {
            "server": "synthetic.invalid",
            "realm_id": 1,
            "stream": "channel",
            "topic": "test",
            "mentioned_user_group_name": "گروه",
        }
        for lang in ("en", "fr"):
            actor = UserProfile(default_language=lang)
            with patch.object(push_notifications, "get_apns_badge_count", return_value=2):
                with patch.object(
                    native_push_text, "_apns_alert_subtitle_backend", ModuleType("old")
                ):
                    baseline = get_message_payload_apns(
                        shared,
                        actor,
                        message,
                        NotificationTriggers.MENTION,
                        can_access_sender=False,
                    )
                native = get_message_payload_apns(
                    shared, actor, message, NotificationTriggers.MENTION, can_access_sender=False
                )
            self.assertEqual(native, baseline)
            self.assertNotIn(sender.full_name, native["alert"]["subtitle"])
            self.assertIn("گروه", native["alert"]["subtitle"])

    def test_current_native_receives_the_selected_sender_label(self) -> None:
        backend = native_push_text._apns_alert_subtitle_backend
        if backend is None:
            self.skipTest("requires current APNS subtitle contract")
        sender = UserProfile(full_name="Hidden Original")
        message = Message(sender=sender, recipient=Recipient(type=Recipient.STREAM))
        with patch.object(
            backend, "render_prepared_apns_subtitle", wraps=backend.render_prepared_apns_subtitle
        ) as render:
            result = get_apns_alert_subtitle(message, "ordinary", can_access_sender=False)
        render.assert_called_once()
        self.assertEqual(render.call_args.args[0], str(UserProfile.INACCESSIBLE_USER_NAME))
        self.assertEqual(result, str(UserProfile.INACCESSIBLE_USER_NAME) + ":")
