from collections.abc import Iterator
from types import ModuleType
from unittest.mock import patch

import orjson
from django.test import SimpleTestCase

from zerver.lib import export
from zerver.lib.export import orjson_serialize_iterable, orjson_stream


class ExportJsonFramingRustTest(SimpleTestCase):
    def test_segment_boundaries_and_nested_data(self) -> None:
        rows = [{"topic": "موضوع\nبحث", "name": "🙂"}, [1, 2], "plain"]
        for n in range(len(rows) + 1):
            for chunk_size in (1, 2, 9):
                for indent in (b"", b"  ", b"\t", b"\n \t"):
                    with self.subTest(n=n, chunk_size=chunk_size, indent=indent):
                        with patch.object(export, "_rust_export_json_framing", ModuleType("old")):
                            expected = list(
                                orjson_stream(rows[:n], indent=indent, chunk_size=chunk_size)
                            )
                        actual = list(orjson_stream(rows[:n], indent=indent, chunk_size=chunk_size))
                        self.assertEqual(actual, expected)
                        self.assertEqual(orjson.loads(b"".join(actual)), rows[:n])

    def test_separator_is_yielded_before_old_serialization_error(self) -> None:
        seen = []

        def rows() -> Iterator[object]:
            seen.append(0)
            yield {"name": "valid"}
            seen.append(1)
            yield object()
            seen.append(2)
            yield "not consumed"

        stream = orjson_stream(rows(), chunk_size=1, indent=b" ")
        self.assertEqual(seen, [])
        self.assertRegex(next(stream), rb"^\[\n")
        self.assertEqual(seen, [0])
        self.assertEqual(next(stream), b",\n ")
        self.assertEqual(seen, [0, 1])
        with self.assertRaises(TypeError):
            next(stream)
        self.assertEqual(seen, [0, 1])

    def test_invalid_framing_is_still_assertion_and_embedded_stream_matches(self) -> None:
        with self.assertRaises(AssertionError):
            list(orjson_stream([0], options=0))
        values = ["a", "b", "فارسی"]
        with patch.object(export, "_rust_export_json_framing", None):
            expected = orjson.dumps({"rows": orjson_serialize_iterable(iter(values), indent=b"  ")})
        self.assertEqual(
            orjson.dumps({"rows": orjson_serialize_iterable(iter(values), indent=b"  ")}), expected
        )
        self.assertEqual(orjson.loads(expected), {"rows": values})

    def test_current_native_only_gets_byte_frames(self) -> None:
        native = export._rust_export_json_framing
        if native is None:
            self.skipTest("requires the current native export framing contract")
        with patch.object(
            native, "frame_prepared_export_json", wraps=native.frame_prepared_export_json
        ) as frame:
            self.assertEqual(len(list(orjson_stream([1, 2], chunk_size=1))), 4)
        self.assertEqual(
            [call.args[0] for call in frame.call_args_list],
            ["first", "separator", "following", "end"],
        )
