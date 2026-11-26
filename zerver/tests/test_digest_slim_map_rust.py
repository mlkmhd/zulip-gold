from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from zerver.lib import digest
from zerver.models import Stream


class DigestSlimMapRustTest(TestCase):
    def test_selected_fields_order_duplicate_and_object_identity(self) -> None:
        first = SimpleNamespace(id=8, name="first")
        later = SimpleNamespace(id=8, name="last")
        other = SimpleNamespace(id=1, name="آزمون")
        query = Mock(only=Mock(return_value=iter([first, other, later])))
        with patch.object(Stream.objects, "filter", return_value=query) as select:
            result = digest.get_slim_stream_id_map({1, 8})
        select.assert_called_once_with(id__in={1, 8})
        query.only.assert_called_once_with("id", "name")
        self.assertEqual(list(result), [8, 1])
        self.assertIs(result[8], later)
        self.assertIs(result[1], other)

    def test_current_native_gets_only_preselected_objects(self) -> None:
        native = digest._rust_index_prepared_slim_channels
        if native is None:
            self.skipTest("requires the current native slim channel index")
        iterator = iter([SimpleNamespace(id=1, name="sample")])
        query = Mock(only=Mock(return_value=iterator))
        with (
            patch.object(Stream.objects, "filter", return_value=query),
            patch.object(digest, "_rust_index_prepared_slim_channels", wraps=native) as index,
        ):
            self.assertEqual(list(digest.get_slim_stream_id_map({1})), [1])
        index.assert_called_once_with(iterator)
