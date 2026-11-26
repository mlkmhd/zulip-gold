from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import reminders, topic_link_util


class ReminderTopicCaptionContractTest(SimpleTestCase):
    def test_ordinary_label_and_original_url_match_python(self) -> None:
        channel = "channel [آ]"
        topic = "topic $$ & * \ud800"
        url = "#narrow/channel/8-example/topic/demo/with/5"
        with (
            patch.object(reminders, "_rust_reminder_topic_caption", None),
            patch.object(reminders, "_rust_reminder_context", None),
        ):
            expected = reminders._reminder_topic_link(channel, topic, url)
        self.assertEqual(reminders._reminder_topic_link(channel, topic, url), expected)
        self.assertTrue(expected.endswith(f"]({url})"))

    def test_native_default_preserves_custom_mapping(self) -> None:
        backend = reminders._rust_reminder_topic_caption
        if getattr(backend, "REMINDER_TOPIC_CAPTION_API", None) != 1:
            self.skipTest("Matching Rust reminder topic caption contract is not available")
        url = "#narrow/sample"
        with (
            patch.object(reminders, "_rust_reminder_context", None),
            patch.object(
                backend,
                "render_exact_reminder_topic_caption",
                wraps=backend.render_exact_reminder_topic_caption,
            ) as native,
        ):
            self.assertEqual(
                reminders._reminder_topic_link("Vanak", "a&b", url),
                "[#Vanak > a&amp;b](#narrow/sample)",
            )
            with patch.dict(topic_link_util.escape_mapping, {"&": " and "}):
                self.assertEqual(
                    reminders._reminder_topic_link("Vanak", "a&b", url),
                    "[#Vanak > a and b](#narrow/sample)",
                )
        native.assert_called_once_with("Vanak", "a&b", url)
