from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import native_recent_conversations
from zerver.lib.message import RecentPrivateConversationDict


class RecentDMResponseContractTest(SimpleTestCase):
    def test_preselected_order_and_ties_keep_existing_output_objects(self) -> None:
        backend = native_recent_conversations._finalize_backend
        if getattr(backend, "RECENT_DM_RESPONSE_API", None) != 1:
            self.skipTest("Matching Rust recent DM response contract is not available")
        mod_id = int("1004")
        rows: dict[frozenset[int], RecentPrivateConversationDict] = {
            frozenset({7, 3}): {"max_message_id": mod_id, "pinned": True},
            frozenset({4}): {"max_message_id": mod_id, "pinned": False},
            frozenset(): {"max_message_id": -(2**63), "pinned": False},
        }
        with (
            patch.object(native_recent_conversations, "_backend", None),
            patch.object(native_recent_conversations, "_assembly_backend", None),
            patch.object(
                backend,
                "finalize_recent_conversations",
                wraps=backend.finalize_recent_conversations,
            ) as finalize,
        ):
            self.assertFalse(native_recent_conversations.assembly_available())
            self.assertEqual(
                native_recent_conversations.finalize(rows),
                [
                    {"user_ids": [3, 7], "max_message_id": mod_id, "pinned": True},
                    {"user_ids": [4], "max_message_id": mod_id, "pinned": False},
                    {"user_ids": [], "max_message_id": -(2**63), "pinned": False},
                ],
            )
        finalize.assert_called_once_with(rows)
