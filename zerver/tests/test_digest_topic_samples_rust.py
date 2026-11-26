from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import digest
from zerver.models import Message, UserProfile


class DigestTopicSamplesRustTest(SimpleTestCase):
    def test_same_first_two_samples_and_human_count(self) -> None:
        backend = digest._rust_topic_sampling
        if backend is None:
            self.skipTest("No native digest samples ABI")
        first = Message(sender=UserProfile(full_name="bot-only", is_bot=True))
        second = Message(sender=UserProfile(full_name="فرستنده"))
        third = Message(sender=UserProfile(full_name="فرستنده"))
        fourth = Message(sender=UserProfile(full_name="independent"))
        messages = (first, second, third, fourth)
        expected = digest.DigestTopic((1, "topic"))
        with patch.object(digest, "_rust_topic_sampling", None):
            for message in messages:
                expected.add_message(message)
        actual = digest.DigestTopic((1, "topic"))
        sample_list = actual.sample_messages
        sender_set = actual.human_senders
        with patch.object(
            backend,
            "add_prepared_digest_topic_message",
            wraps=backend.add_prepared_digest_topic_message,
        ) as add:
            for message in messages:
                actual.add_message(message)
        self.assertEqual(add.call_count, len(messages))
        self.assertIs(actual.sample_messages, sample_list)
        self.assertIs(actual.human_senders, sender_set)
        self.assertEqual(len(actual.sample_messages), 2)
        self.assertIs(actual.sample_messages[0], first)
        self.assertIs(actual.sample_messages[1], second)
        self.assertEqual(actual.length(), expected.length())
        self.assertEqual(actual.human_senders, expected.human_senders)
        self.assertEqual(actual.length(), 3)
        self.assertEqual(actual.human_senders, {"فرستنده", "independent"})
