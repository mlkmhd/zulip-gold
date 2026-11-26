from unittest import TestCase
from unittest.mock import Mock, call, patch

from markupsafe import Markup

from zerver.lib import digest
from zerver.models import Stream, UserProfile


class DigestMessageContextRustTest(TestCase):
    def test_selects_only_the_prepared_fields_and_preserves_identity(self) -> None:
        user = Mock(spec=UserProfile)
        streams: dict[int, Stream] = {}
        channels: dict[str, list[Markup] | list[str]] = {
            "html": [Markup("بخش &amp; نمونه")],
            "plain": ["بخش & نمونه"],
        }
        first = Mock(spec=digest.DigestTopic)
        first.teaser_data.return_value = object()
        second = Mock(spec=digest.DigestTopic)
        second.teaser_data.return_value = object()
        callbacks = Mock()
        callbacks.attach_mock(first.teaser_data, "first")
        callbacks.attach_mock(second.teaser_data, "second")

        hidden = digest._build_prepared_digest_message_context(user, streams, channels, 2**90, None)
        self.assertEqual(
            list(hidden), ["new_messages_count", "hot_conversations", "show_message_content"]
        )
        self.assertEqual(
            hidden,
            {"new_messages_count": 2**90, "hot_conversations": [], "show_message_content": False},
        )
        self.assertEqual(callbacks.mock_calls, [])

        empty = digest._build_prepared_digest_message_context(user, streams, channels, 0, [])
        self.assertIs(empty["show_message_content"], True)
        self.assertEqual(empty["hot_conversations"], [])
        self.assertIs(empty["new_channels"], channels)

        visible = digest._build_prepared_digest_message_context(
            user, streams, channels, 10, [first, second, first]
        )
        self.assertEqual(
            list(visible),
            ["hot_conversations", "new_channels", "new_messages_count", "show_message_content"],
        )
        self.assertEqual(
            visible["hot_conversations"],
            [
                first.teaser_data.return_value,
                second.teaser_data.return_value,
                first.teaser_data.return_value,
            ],
        )
        self.assertIs(visible["new_channels"], channels)
        self.assertEqual(
            callbacks.mock_calls,
            [call.first(user, streams), call.second(user, streams), call.first(user, streams)],
        )

    def test_stops_after_original_teaser_error(self) -> None:
        user = Mock(spec=UserProfile)
        first = Mock(spec=digest.DigestTopic)
        failure = ValueError("synthetic teaser error")
        first.teaser_data.side_effect = failure
        second = Mock(spec=digest.DigestTopic)
        with self.assertRaises(ValueError) as raised:
            digest._build_prepared_digest_message_context(user, {}, {}, 0, [first, second])
        self.assertIs(raised.exception, failure)
        second.teaser_data.assert_not_called()

    def test_current_extension_handles_the_prepared_display_branch(self) -> None:
        native = digest._rust_build_prepared_digest_message_context
        if native is None:
            self.skipTest("requires the current native digest presentation helper")
        user = Mock(spec=UserProfile)
        with patch.object(
            digest, "_rust_build_prepared_digest_message_context", wraps=native
        ) as build:
            digest._build_prepared_digest_message_context(user, {}, {}, 0, [])
        build.assert_called_once_with(user, {}, {}, 0, [])
