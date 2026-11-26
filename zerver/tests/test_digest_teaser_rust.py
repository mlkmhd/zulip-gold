from types import ModuleType
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from zerver.lib import digest
from zerver.lib.digest import DigestTeaserData, DigestTopic
from zerver.lib.email_notifications import MessageListPayload
from zerver.models import Message, Stream, UserProfile


class DigestTeaserDjangoRustTest(SimpleTestCase):
    def test_teaser_keeps_sample_objects_and_construction_order(self) -> None:
        user = UserProfile()
        early = Message(id=1)
        later = Message(id=2)
        streams = {12: Stream(id=12)}
        rendered = MessageListPayload(senders=[])
        for count, expected_remaining in ((0, -2), (2**90, 2**90 - 2)):
            teaser = DigestTopic((12, "example"))
            teaser.num_human_messages = count
            teaser.human_senders = {"تینا", "A", "a"}
            teaser.sample_messages = [later, early]

            def render(
                *,
                user: UserProfile,
                messages: list[Message],
                stream_id_map: dict[int, Stream],
                teaser: DigestTopic = teaser,
            ) -> MessageListPayload:
                self.assertIs(messages, teaser.sample_messages)
                self.assertIs(stream_id_map, streams)
                self.assertEqual([m.id for m in messages], [2, 1])
                messages.reverse()
                messages.pop()
                teaser.human_senders.add("é")
                return rendered

            mock = Mock(side_effect=render)
            with patch.object(digest, "build_message_list", mock):
                result = teaser.teaser_data(user, streams)
            self.assertEqual(
                result, DigestTeaserData(["A", "a", "é", "تینا"], expected_remaining, rendered)
            )
            self.assertIs(result.first_few_messages, rendered)
            self.assertEqual(teaser.sample_messages, [early])
            mock.assert_called_once_with(
                user=user, messages=teaser.sample_messages, stream_id_map=streams
            )

    def test_old_backend_or_renderer_error_does_not_retry(self) -> None:
        topic = DigestTopic((1, "example"))
        user = UserProfile()
        with (
            patch.object(digest, "_rust_topic_teaser", ModuleType("old")),
            patch.object(digest, "build_message_list", return_value=MessageListPayload(senders=[])),
        ):
            self.assertEqual(topic.teaser_data(user, {}).participants, [])

        with (
            patch.object(
                digest, "build_message_list", side_effect=ValueError("synthetic render failure")
            ) as render,
            self.assertRaisesRegex(ValueError, "^synthetic render failure$"),
        ):
            topic.teaser_data(user, {})
        render.assert_called_once_with(user=user, messages=[], stream_id_map={})

    def test_django_calls_current_native_entrypoint(self) -> None:
        native = digest._rust_topic_teaser
        if native is None:
            self.skipTest("requires the current native digest teaser contract")
        user = UserProfile()
        topic = DigestTopic((1, "example"))
        rendered = MessageListPayload(senders=[])
        with (
            patch.object(
                native, "build_prepared_digest_teaser", wraps=native.build_prepared_digest_teaser
            ) as call,
            patch.object(digest, "build_message_list", return_value=rendered),
        ):
            self.assertEqual(topic.teaser_data(user, {}), DigestTeaserData([], 0, rendered))
        call.assert_called_once()
