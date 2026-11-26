from collections import defaultdict
from collections.abc import Iterator
from types import ModuleType
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from zerver.lib import digest
from zerver.models import UserTopic


class DigestMutedTopicRowsDjangoRustTest(SimpleTestCase):
    def test_muted_policy_query_and_prepared_grouping(self) -> None:
        rows = [
            {"user_profile_id": 3, "stream_id": 8, "topic_name": "İSTANBUL"},
            {"user_profile_id": 1, "stream_id": 4, "topic_name": "ΟΣ"},
            {"user_profile_id": 3, "stream_id": 8, "topic_name": "i\u0307stanbul"},
            {"user_profile_id": 3, "stream_id": 4, "topic_name": ""},
            {"user_profile_id": 3, "stream_id": 4, "topic_name": "\ud800A"},
        ]
        values = Mock(return_value=iter(rows))
        with patch.object(UserTopic.objects, "filter", return_value=Mock(values=values)) as query:
            actual = digest.get_user_muted_topics_map([3, 1])
        query.assert_called_once_with(
            user_profile_id__in=[3, 1], visibility_policy=UserTopic.VisibilityPolicy.MUTED
        )
        values.assert_called_once_with("user_profile_id", "stream_id", "topic_name")
        self.assertEqual(list(actual), [3, 1])
        self.assertEqual(actual[3], {(8, "i\u0307stanbul"), (4, ""), (4, "\ud800a")})
        self.assertEqual(actual[1], {(4, "ος")})
        self.assertIsInstance(actual, defaultdict)
        assert isinstance(actual, defaultdict)
        self.assertIs(actual.default_factory, set)
        self.assertEqual(actual[99], set())

    def test_old_contract_and_row_iteration_error(self) -> None:
        rows = Mock(values=Mock(return_value=[]))
        with (
            patch.object(digest, "_rust_muted_topic_rows", ModuleType("old")),
            patch.object(UserTopic.objects, "filter", return_value=rows),
        ):
            self.assertEqual(digest.get_user_muted_topics_map([]), defaultdict(set))

        def incomplete_rows() -> Iterator[dict[str, int | str]]:
            yield {"user_profile_id": 3, "stream_id": 8, "topic_name": "A"}
            raise ValueError("synthetic row failure")

        rows.values = Mock(return_value=incomplete_rows())
        with (
            patch.object(UserTopic.objects, "filter", return_value=rows),
            self.assertRaisesRegex(ValueError, "^synthetic row failure$"),
        ):
            digest.get_user_muted_topics_map([3])
        rows.values.assert_called_once_with("user_profile_id", "stream_id", "topic_name")

    def test_current_extension_is_used(self) -> None:
        native = digest._rust_muted_topic_rows
        if native is None:
            self.skipTest("requires the current native muted topic rows contract")
        values = Mock(return_value=[])
        with (
            patch.object(UserTopic.objects, "filter", return_value=Mock(values=values)),
            patch.object(
                native,
                "group_prepared_digest_muted_topic_rows",
                wraps=native.group_prepared_digest_muted_topic_rows,
            ) as group,
        ):
            self.assertEqual(digest.get_user_muted_topics_map([]), defaultdict(set))
        group.assert_called_once_with([], defaultdict(set))
