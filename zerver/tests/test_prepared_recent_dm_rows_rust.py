from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext

from zerver.lib import native_recent_conversations
from zerver.lib.message import get_recent_private_conversations
from zerver.lib.test_classes import ZulipTestCase


class PreparedRecentDMRowsContractTest(ZulipTestCase):
    def test_prepared_rows_keep_the_same_selection_and_queries(self) -> None:
        owner = self.example_user("hamlet")
        peer = self.example_user("cordelia")
        message_id = self.send_personal_message(owner, peer)

        with (
            patch.object(native_recent_conversations, "_backend", None),
            patch.object(native_recent_conversations, "_assembly_backend", None),
            CaptureQueriesContext(connection) as reference_queries,
        ):
            expected = get_recent_private_conversations(owner)
        with CaptureQueriesContext(connection) as actual_queries:
            actual = get_recent_private_conversations(owner)
        self.assertEqual(list(actual.items()), list(expected.items()))
        self.assertEqual(actual[frozenset({peer.id})]["max_message_id"], message_id)
        self.assertGreater(len(reference_queries), 0)
        self.assertEqual(
            [query["sql"] for query in actual_queries.captured_queries],
            [query["sql"] for query in reference_queries.captured_queries],
        )
