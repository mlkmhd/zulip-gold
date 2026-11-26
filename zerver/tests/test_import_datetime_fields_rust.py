from copy import deepcopy
from datetime import datetime, timezone
from types import ModuleType
from unittest.mock import call, patch

from django.test import SimpleTestCase

from zerver.lib import import_realm
from zerver.lib.import_realm import ImportedTableData, fix_datetime_fields


class ImportDatetimeFieldsRustTest(SimpleTestCase):
    def test_prepared_rows_keep_order_offsets_and_none(self) -> None:
        data: ImportedTableData = {
            "synthetic": [
                {"ordered": "موضوع", "first": -12.345678, "second": None},
                {"ordered": "other", "first": 0, "second": 302.000001},
            ]
        }
        reference = deepcopy(data)
        with patch.object(
            import_realm, "date_fields_for_table", return_value=["first", "second"]
        ) as schema:
            with patch.object(import_realm, "_rust_import_datetime_fields", ModuleType("old")):
                fix_datetime_fields(reference, "synthetic")
            schema.reset_mock()
            fix_datetime_fields(data, "synthetic")
        self.assertEqual(data, reference)
        self.assertEqual(schema.call_args_list, [call("synthetic"), call("synthetic")])
        self.assertEqual(tuple(data["synthetic"][0]), ("ordered", "first", "second"))
        self.assertIsNone(data["synthetic"][0]["second"])
        self.assertEqual(
            data["synthetic"][0]["first"], datetime.fromtimestamp(-12.345678, tz=timezone.utc)
        )

    def test_exact_partial_mutation_and_missing_field_error(self) -> None:
        data: ImportedTableData = {"synthetic": [{"first": 1.0}, {"first": 2.0, "second": 3.0}]}
        row = data["synthetic"][0]
        with (
            patch.object(
                import_realm, "date_fields_for_table", return_value=["first", "second"]
            ) as schema,
            self.assertRaises(KeyError) as error,
        ):
            fix_datetime_fields(data, "synthetic")
        self.assertEqual(error.exception.args, ("second",))
        self.assertEqual(row["first"], datetime.fromtimestamp(1.0, tz=timezone.utc))
        self.assertEqual(data["synthetic"][1], {"first": 2.0, "second": 3.0})
        schema.assert_called_once_with("synthetic")

    def test_current_extension_handles_empty_or_prepared_rows(self) -> None:
        backend = import_realm._rust_import_datetime_fields
        if backend is None:
            self.skipTest("requires current native import datetime fields")
        data: ImportedTableData = {"synthetic": []}
        with (
            patch.object(import_realm, "date_fields_for_table") as schema,
            patch.object(
                backend,
                "restore_prepared_import_datetimes",
                wraps=backend.restore_prepared_import_datetimes,
            ) as restore,
        ):
            fix_datetime_fields(data, "synthetic")
        restore.assert_called_once()
        self.assertIs(restore.call_args.args[0], data["synthetic"])
        schema.assert_not_called()
