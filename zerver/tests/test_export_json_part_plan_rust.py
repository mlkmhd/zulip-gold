import gc
import weakref
from collections.abc import Generator, Iterator
from types import ModuleType
from typing import cast
from unittest.mock import patch

import orjson
from django.test import SimpleTestCase

from zerver.lib import export
from zerver.lib.export import orjson_stream


class ExportJsonPartPlanRustTest(SimpleTestCase):
    def test_boundary_is_before_encoding_and_send_does_not_forward(self) -> None:
        trace: list[str] = []

        def source() -> Iterator[int]:
            trace.append("source 1")
            yield 1
            trace.append("source 2")
            yield 2

        serializer = orjson.dumps

        def encode(items: tuple[int, ...], *, option: int) -> bytes:
            trace.append(f"encode {items[0]}")
            return serializer(items, option=option)

        with patch.object(orjson, "dumps", side_effect=encode):
            stream = cast("Generator[bytes, object, None]", orjson_stream(source(), chunk_size=1))
            self.assertEqual(trace, [])
            self.assertEqual(next(stream), b"[\n  1")
            self.assertEqual(trace, ["source 1", "encode 1"])
            self.assertEqual(stream.send("ignored"), b",\n")
            self.assertEqual(trace, ["source 1", "encode 1", "source 2"])
            self.assertEqual(next(stream), b"  2")
            self.assertEqual(trace, ["source 1", "encode 1", "source 2", "encode 2"])
            self.assertEqual(next(stream), b"\n]")
            with self.assertRaises(StopIteration):
                next(stream)

    def test_source_exception_has_no_separator_and_closes_stream(self) -> None:
        def source() -> Iterator[int]:
            yield 1
            raise ValueError("source stopped with error")

        stream = orjson_stream(source(), chunk_size=1)
        self.assertEqual(next(stream), b"[\n  1")
        with self.assertRaisesRegex(ValueError, "^source stopped with error$"):
            next(stream)
        with self.assertRaises(StopIteration):
            next(stream)

    def test_legacy_invalid_batch_size_and_empty_output(self) -> None:
        self.assertEqual(list(orjson_stream([1, 2], chunk_size=0)), [b"[]"])
        with (
            patch.object(export, "_rust_export_json_part_plan", ModuleType("old")),
            self.assertRaises(ValueError) as expected,
        ):
            list(orjson_stream([1], chunk_size=-1))
        with self.assertRaises(ValueError) as actual:
            list(orjson_stream([1], chunk_size=-1))
        self.assertEqual(actual.exception.args, expected.exception.args)

    def test_suspended_source_cycle_remains_collectable(self) -> None:
        def abandoned() -> tuple[
            weakref.ReferenceType[Iterator[int]], weakref.ReferenceType[Iterator[bytes]]
        ]:
            def source() -> Iterator[int]:
                yield 1
                yield id(stream)

            rows = source()
            stream = orjson_stream(rows, chunk_size=1)
            self.assertEqual(next(stream), b"[\n  1")
            return weakref.ref(rows), weakref.ref(stream)

        source, stream = abandoned()
        gc.collect()
        self.assertIsNone(source())
        self.assertIsNone(stream())

    def test_current_extension_plans_exclusively_through_existing_lazy_batches(self) -> None:
        native = export._rust_export_json_part_plan
        if native is None:
            self.skipTest("requires current native export part planning")
        with patch.object(
            native, "plan_prepared_export_parts", wraps=native.plan_prepared_export_parts
        ) as plan:
            stream = orjson_stream([1, 2], chunk_size=1)
            plan.assert_not_called()
            self.assertEqual(list(stream), [b"[\n  1", b",\n", b"  2", b"\n]"])
        plan.assert_called_once()
