from datetime import datetime, timezone
from types import ModuleType
from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase

from zerver.lib import digest
from zerver.lib.digest import DigestTopic


class DigestRecentSelectionDjangoRustTest(SimpleTestCase):
    def test_prepared_stream_boundary_mute_keys_and_order(self) -> None:
        cutoff = datetime(2026, 11, 26, tzinfo=timezone.utc)
        stream_ids = {17, 32}
        muted = DigestTopic((17, "İSTANBUL"))
        keep = DigestTopic((32, "İSTANBUL"))
        empty = DigestTopic((17, ""))
        sigma = DigestTopic((32, "ΟΣ"))
        rows = {17: [muted, empty, empty], 32: [keep, sigma]}
        query = Mock(side_effect=lambda realm, stream, timestamp: rows[stream])
        preferences = {(17, "i\u0307stanbul"), (32, "ος")}
        expected = [
            topic for stream in stream_ids for topic in ({17: [empty, empty], 32: [keep]}[stream])
        ]
        with patch.object(digest, "get_recent_topics", query):
            result = digest._collect_recent_digest_topics(9, stream_ids, preferences, cutoff)
            self.assertEqual(result, expected)
            self.assertEqual(
                query.call_args_list, [call(9, stream, cutoff) for stream in stream_ids]
            )
            query.reset_mock()
            self.assertEqual(
                digest._collect_recent_digest_topics(9, set(), preferences, cutoff), []
            )
            query.assert_not_called()

    def test_old_contract_and_single_query_error(self) -> None:
        cutoff = datetime(2026, 11, 26, tzinfo=timezone.utc)
        with (
            patch.object(digest, "_rust_recent_topic_selection", ModuleType("old")),
            patch.object(digest, "get_recent_topics", return_value=[DigestTopic((3, "MUTED"))]),
        ):
            self.assertEqual(
                digest._collect_recent_digest_topics(9, {3}, {(3, "muted")}, cutoff), []
            )
        broken = Mock(side_effect=ValueError("synthetic query failure"))
        with (
            patch.object(digest, "get_recent_topics", broken),
            self.assertRaisesRegex(ValueError, "^synthetic query failure$"),
        ):
            digest._collect_recent_digest_topics(9, {3, 10}, set(), cutoff)
        self.assertEqual(broken.call_count, 1)

    def test_current_extension_is_used(self) -> None:
        native = digest._rust_recent_topic_selection
        if native is None:
            self.skipTest("requires the current native recent topic selection contract")
        cutoff = datetime(2026, 11, 26, tzinfo=timezone.utc)
        with patch.object(
            native,
            "collect_prepared_digest_recent_topics",
            wraps=native.collect_prepared_digest_recent_topics,
        ) as collect:
            self.assertEqual(digest._collect_recent_digest_topics(9, set(), set(), cutoff), [])
        collect.assert_called_once_with(set(), set(), 9, cutoff, digest.get_recent_topics)
