from datetime import datetime, timedelta, timezone
from types import ModuleType
from unittest.mock import patch
from zoneinfo import ZoneInfo

import orjson
from django.test import SimpleTestCase
from django.utils.timezone import is_naive
from typing_extensions import override

from zerver.lib import export
from zerver.lib.export import Record, date_fields_for_table, floatify_datetime_fields


class ExportDatetimeFieldsRustTest(SimpleTestCase):
    def test_original_table_fields_keep_timestamp_precision_none_and_key_order(self) -> None:
        table = "zerver_message"
        fields = date_fields_for_table(table)
        self.assertIn("date_sent", fields)
        aware = datetime(1966, 6, 2, 10, 47, 11, 345678, tzinfo=timezone(timedelta(hours=-3)))
        row: Record = dict.fromkeys(fields)
        row.update(date_sent=aware, untouched={"data": "موضوع"})
        with patch.object(export, "_rust_export_datetime_fields", ModuleType("old")):
            expected = floatify_datetime_fields(row, table)
        result = floatify_datetime_fields(row, table)
        self.assertEqual(orjson.dumps(result), orjson.dumps(expected))
        self.assertEqual(tuple(result), tuple(row))
        self.assertEqual(result["date_sent"], aware.timestamp())
        self.assertIs(row["date_sent"], aware)
        self.assertIs(result["untouched"], row["untouched"])

    def test_invalid_fields_and_datetime_subclasses_keep_legacy_behavior(self) -> None:
        table = "synthetic"
        aware = datetime(2025, 11, 2, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1)
        with patch.object(export, "date_fields_for_table", return_value=["second", "first"]):
            row = {"first": aware, "second": aware.replace(fold=0)}
            result = floatify_datetime_fields(row, table)
            self.assertEqual(result["first"] - result["second"], 3600)
            with self.assertRaises(KeyError) as error:
                floatify_datetime_fields({"first": aware}, table)
            self.assertEqual(error.exception.args, ("second",))
            for invalid in ("not datetime", aware.replace(tzinfo=None)):
                with self.assertRaises(AssertionError) as invalid_error:
                    floatify_datetime_fields({"first": aware, "second": invalid}, table)
                self.assertEqual(invalid_error.exception.args, ())

    def test_timestamp_method_of_datetime_subclass_is_called_once(self) -> None:
        events: list[str] = []

        class ReportedDatetime(datetime):
            @override
            def timestamp(self) -> float:
                events.append("timestamp")
                return 2.125

        value = ReportedDatetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch.object(export, "date_fields_for_table", return_value=["time"]):
            self.assertEqual(
                floatify_datetime_fields({"time": value}, "synthetic"), {"time": 2.125}
            )
        self.assertEqual(events, ["timestamp"])

    def test_current_extension_receives_prepared_local_schema(self) -> None:
        backend = export._rust_export_datetime_fields
        if backend is None:
            self.skipTest("requires current native export datetime fields contract")
        row = {"time": datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)}
        with (
            patch.object(export, "date_fields_for_table", return_value=["time"]) as schema,
            patch.object(
                backend,
                "format_prepared_export_datetimes",
                wraps=backend.format_prepared_export_datetimes,
            ) as convert,
        ):
            self.assertEqual(floatify_datetime_fields(row, "synthetic"), {"time": 1.0})
        schema.assert_called_once_with("synthetic")
        convert.assert_called_once_with(row, ["time"], datetime, is_naive)
