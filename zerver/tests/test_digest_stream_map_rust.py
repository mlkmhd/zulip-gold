from collections import defaultdict
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import Mock, patch

from zerver.lib import digest
from zerver.models import Recipient, Subscription


class DigestStreamMapRustTest(TestCase):
    def test_actual_query_predicates_are_not_part_of_rust(self) -> None:
        cutoff = datetime(2026, 11, 26, tzinfo=timezone.utc)
        query = Mock()
        values = query.alias.return_value.filter.return_value.values
        values.return_value = iter(
            [
                {"user_profile_id": 7, "recipient__type_id": 42},
                {"user_profile_id": 5, "recipient__type_id": 43},
                {"user_profile_id": 7, "recipient__type_id": 42},
            ]
        )
        with patch.object(Subscription.objects, "filter", return_value=query) as select:
            grouped = digest.get_user_stream_map([5, 7], cutoff)
        select.assert_called_once_with(
            user_profile_id__in=[5, 7],
            recipient__type=Recipient.STREAM,
            active=True,
            is_muted=False,
        )
        query.alias.assert_called_once()
        query.alias.return_value.filter.assert_called_once_with(was_modified=False)
        values.assert_called_once_with("user_profile_id", "recipient__type_id")
        self.assertEqual(list(grouped.items()), [(7, {42}), (5, {43})])
        self.assertEqual(type(grouped), defaultdict)
        self.assertEqual(grouped[123], set())

    def test_original_row_error_is_preserved_without_retrying_query(self) -> None:
        cutoff = datetime(2026, 11, 26, tzinfo=timezone.utc)
        query = Mock()
        values = query.alias.return_value.filter.return_value.values
        values.return_value = iter([{"user_profile_id": 7}])
        with (
            patch.object(Subscription.objects, "filter", return_value=query),
            self.assertRaises(KeyError) as error,
        ):
            digest.get_user_stream_map([7], cutoff)
        self.assertEqual(error.exception.args, ("recipient__type_id",))
        values.assert_called_once_with("user_profile_id", "recipient__type_id")

    def test_new_native_function_is_used_only_after_query_construction(self) -> None:
        native = digest._rust_group_prepared_digest_stream_rows
        if native is None:
            self.skipTest("requires current native stream-map grouping")
        cutoff = datetime(2026, 11, 26, tzinfo=timezone.utc)
        query = Mock()
        query.alias.return_value.filter.return_value.values.return_value = iter([])
        with (
            patch.object(Subscription.objects, "filter", return_value=query),
            patch.object(digest, "_rust_group_prepared_digest_stream_rows", wraps=native) as group,
        ):
            self.assertEqual(digest.get_user_stream_map([], cutoff), {})
        group.assert_called_once()
