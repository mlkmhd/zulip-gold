from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now as timezone_now

from zerver.lib import digest
from zerver.lib.test_classes import ZulipTestCase


class DigestTopicAggregationRustTest(ZulipTestCase):
    def test_recent_topics_keep_one_query_insertion_order_and_first_two_messages(self) -> None:
        backend = digest._rust_recent_topic_aggregation
        if backend is None:
            self.skipTest("No prepared Rust digest aggregation ABI")

        sender = self.example_user("hamlet")
        other = self.example_user("othello")
        stream_name = "Verona"
        stream_id = self.get_stream_id(stream_name)
        cutoff = timezone_now() - timedelta(seconds=1)
        topic_names = ("گفتگو", "گفتگو متفاوت", "گفتگو", "گفتگو")
        ids = [
            self.send_stream_message(author, stream_name, str(i), topic_name)
            for i, (author, topic_name) in enumerate(
                zip((sender, sender, other, sender), topic_names, strict=True)
            )
        ]
        self.addCleanup(digest.get_recent_topics.cache_clear)

        with (
            patch.object(digest, "_rust_recent_topic_aggregation", None),
            CaptureQueriesContext(connection) as expected_queries,
        ):
            digest.get_recent_topics.cache_clear()
            expected = digest.get_recent_topics(sender.realm_id, stream_id, cutoff)

        with (
            patch.object(
                backend,
                "assemble_recent_digest_topics",
                wraps=backend.assemble_recent_digest_topics,
            ) as assemble,
            CaptureQueriesContext(connection) as actual_queries,
        ):
            digest.get_recent_topics.cache_clear()
            actual = digest.get_recent_topics(sender.realm_id, stream_id, cutoff)

        assemble.assert_called_once()
        self.assertEqual(len(actual_queries), 1)
        self.assertEqual(
            [query["sql"] for query in actual_queries],
            [query["sql"] for query in expected_queries],
        )
        self.assertEqual([topic.topic_name() for topic in actual], list(dict.fromkeys(topic_names)))
        self.assertEqual(
            [(topic.topic_key, topic.human_senders, topic.length()) for topic in actual],
            [(topic.topic_key, topic.human_senders, topic.length()) for topic in expected],
        )
        self.assertEqual(
            [[msg.id for msg in topic.sample_messages] for topic in actual],
            [[ids[0], ids[2]], [ids[1]]],
        )
