import os
from collections.abc import Iterator
from importlib import import_module
from unittest import TestCase
from unittest.mock import Mock, patch

from markdown import Markdown

from zerver.lib.markdown import api_arguments_table_generator as arguments
from zerver.lib.markdown.api_return_values_table_generator import APIReturnValuesTablePreprocessor
from zerver.openapi.openapi import Parameter


class APITypeDisplayRustTest(TestCase):
    def test_native_prepared_labels(self) -> None:
        try:
            native = import_module("_zulip_backend")
            join = native.join_prepared_doc_type_labels
            array = native.wrap_prepared_doc_array_label
            nullable = native.append_doc_nullable_label
        except (ImportError, AttributeError):
            self.skipTest("Rust API documentation type label helpers are not available")
        short = "نوع🙂"
        surrogate = "\ud800"
        self.assertIs(join([short]), short)
        self.assertIs(join([surrogate]), surrogate)
        self.assertEqual(join([]), "")
        self.assertEqual(join(["", "نوع🙂", "مرحله\0"]), " | نوع🙂 | مرحله\0")
        self.assertEqual(array(short), "(نوع🙂)[]")
        self.assertEqual(nullable(short), "نوع🙂 | null")
        self.assertIsNone(join(("string", "integer")))
        self.assertIsNone(join(["string", 7]))
        self.assertIsNone(join(["string", surrogate]))
        self.assertIsNone(array(surrogate))
        self.assertIsNone(nullable(surrogate))

    def test_argument_and_return_value_docs_use_prepared_labels(self) -> None:
        originals = (
            arguments._rust_join_prepared_doc_type_labels,
            arguments._rust_wrap_prepared_doc_array_label,
            arguments._rust_append_doc_nullable_label,
        )
        if os.environ.get("ZULIP_RUST_API_TYPE_DISPLAY") != "1":
            self.assertEqual(originals, (None, None, None))
        join, array, nullable = (Mock(wraps=fn) if fn is not None else None for fn in originals)
        schema = {"items": {"oneOf": [{"type": "string", "nullable": True}, {"type": "integer"}]}}
        parameter = Parameter(
            kind="query",
            name="prepared",
            description="A synthetic type row.",
            json_encoded=True,
            value_schema=schema,
            example="<&",
            required=True,
            deprecated=False,
        )
        argument_preprocessor = arguments.APIArgumentsTablePreprocessor(Markdown(), {})
        result_preprocessor = APIReturnValuesTablePreprocessor(Markdown(), {})
        with (
            patch.object(arguments, "_rust_join_prepared_doc_type_labels", join),
            patch.object(arguments, "_rust_wrap_prepared_doc_array_label", array),
            patch.object(arguments, "_rust_append_doc_nullable_label", nullable),
        ):
            rendered_arguments = "\n".join(argument_preprocessor.render_parameters([parameter]))
            rendered_results = "\n".join(
                result_preprocessor.render_table(
                    {"prepared": {"description": "A synthetic type row.", **schema}}, 0
                )
            )
        expected = '<span class="api-field-type">(string | null | integer)[]</span>'
        self.assertIn(expected, rendered_arguments)
        self.assertIn(expected, rendered_results)
        self.assertIn("&lt;&amp;", rendered_arguments)
        if join is not None:
            self.assertEqual(
                [call.args for call in join.call_args_list], [(["string | null", "integer"],)] * 2
            )
        if array is not None:
            self.assertEqual(
                [call.args for call in array.call_args_list], [("string | null | integer",)] * 2
            )
        if nullable is not None:
            self.assertEqual([call.args for call in nullable.call_args_list], [("string",)] * 2)

    def test_traversal_has_original_order_error_and_identity_semantics(self) -> None:
        seen: list[str] = []

        def children() -> Iterator[dict[str, int | str]]:
            seen.append("first")
            yield {"type": 17}
            seen.append("second")
            yield {"type": "string"}

        with self.assertRaisesRegex(TypeError, "sequence item 0: expected str instance, int found"):
            arguments.generate_data_type({"oneOf": children()})
        self.assertEqual(seen, ["first", "second"])

        with self.assertRaises(KeyError) as missing:
            arguments.generate_data_type({"oneOf": [{"type": 7}, {}]})
        self.assertEqual(missing.exception.args, ("type",))

        original = "مثال🙂"
        self.assertIs(arguments.generate_data_type({"type": original}), original)
        self.assertIs(arguments.generate_data_type({"oneOf": [{"type": original}]}), original)
        self.assertEqual(arguments.generate_data_type({"oneOf": [], "items": {}}), "")
        self.assertEqual(arguments.generate_data_type({"items": {"type": "\ud800"}}), "(\ud800)[]")

    def test_decline_keeps_python_operators_and_rendered_values(self) -> None:
        class Derived(str):
            __slots__ = ()

            def __radd__(self, prefix: str) -> str:
                return prefix + "R"

            def __iadd__(self, suffix: str) -> str:
                return "I:" + suffix

        original = Derived("example")
        with (
            patch.object(
                arguments, "_rust_join_prepared_doc_type_labels", return_value=None
            ) as join,
            patch.object(
                arguments, "_rust_wrap_prepared_doc_array_label", return_value=None
            ) as array,
            patch.object(
                arguments, "_rust_append_doc_nullable_label", return_value=None
            ) as nullable,
        ):
            self.assertEqual(
                arguments.generate_data_type({"oneOf": [{"type": original}]}), "example"
            )
            self.assertEqual(arguments.generate_data_type({"items": {"type": original}}), "(R)[]")
            self.assertEqual(
                arguments.generate_data_type({"type": original, "nullable": True}), "I: | null"
            )
        self.assertIs(join.call_args.args[0][0], original)
        self.assertIs(array.call_args.args[0], original)
        self.assertIs(nullable.call_args.args[0], original)
