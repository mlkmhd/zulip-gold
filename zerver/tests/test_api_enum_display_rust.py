import json
from collections.abc import Iterator
from importlib import import_module
from typing import cast
from unittest import TestCase
from unittest.mock import Mock, patch

from markdown import Markdown
from typing_extensions import override

from zerver.lib.markdown import api_arguments_table_generator as docs
from zerver.openapi.openapi import Parameter


class APIEnumDisplayRustTest(TestCase):
    def test_native_join_and_identity(self) -> None:
        try:
            native = import_module("_zulip_backend")
            join = native.join_prepared_api_enum_examples
        except (AttributeError, ImportError):
            self.skipTest("Rust API documentation enum join is not available")

        text = "tag:آ🙂\r".removeprefix("tag:")
        surrogate = "\ud800"
        self.assertIs(join([text]), text)
        self.assertIs(join([surrogate]), surrogate)
        self.assertEqual(join(["", "seg\0", "آ🙂"]), ", seg\0, آ🙂")
        self.assertEqual(join([]), "")

        class Derived(str):
            __slots__ = ()

        self.assertIsNone(join([Derived("x")]))
        if getattr(native, "API_ENUM_DISPLAY_API", None) == 1:
            self.assertEqual(join([text, surrogate, "\udfff"]), f"{text}, {surrogate}, \udfff")
        self.assertIsNone(join((text,)))
        self.assertIsNone(join([text, 0]))

    def test_real_argument_and_object_call_sites_receive_prepared_examples(self) -> None:
        native = docs._rust_join_prepared_api_enum_examples
        spy = Mock(wraps=native) if native is not None else None
        argument_enum = ["آ", 2, False, None]
        field_enum = ["nested", "فارسی"]
        parameter = Parameter(
            kind="query",
            name="fixture",
            description="Synthetic text.",
            json_encoded=True,
            value_schema={
                "type": "object",
                "enum": argument_enum,
                "properties": {"option": {"type": "string", "enum": field_enum}},
            },
            example="<&",
            required=True,
            deprecated=False,
        )
        generator = docs.APIArgumentsTablePreprocessor(Markdown(), {})
        with patch.object(docs, "_rust_join_prepared_api_enum_examples", None):
            expected = generator.render_parameters([parameter])
        with patch.object(docs, "_rust_join_prepared_api_enum_examples", spy):
            actual = generator.render_parameters([parameter])
        self.assertEqual(actual, expected)
        self.assertIn("&lt;&amp;", actual[0])
        self.assertEqual(actual[0].count("Must be one of:"), 2)
        if spy is not None:
            self.assertEqual(
                [call.args for call in spy.call_args_list],
                [
                    (
                        [
                            docs.OBJECT_CODE_TEMPLATE.format(value=json.dumps(e))
                            for e in argument_enum
                        ],
                    ),
                    ([docs.OBJECT_CODE_TEMPLATE.format(value=json.dumps(e)) for e in field_enum],),
                ],
            )

    def test_old_type_errors_and_lazy_iteration_are_untouched(self) -> None:
        calls: list[str] = []

        class WithIteration(list[str]):
            @override
            def __iter__(self) -> Iterator[str]:
                calls.append("iterated")
                return super().__iter__()

        self.assertEqual(docs._join_api_enum_examples(WithIteration(["آ", "x"])), "آ, x")
        self.assertEqual(calls, ["iterated"])
        with self.assertRaisesRegex(TypeError, "sequence item 1: expected str instance, int found"):
            docs._join_api_enum_examples(cast("list[str]", ["one", 8]))

    def test_decline_and_empty_enums_keep_legacy_output(self) -> None:
        generator = docs.APIArgumentsTablePreprocessor(Markdown(), {})
        with patch.object(
            docs, "_rust_join_prepared_api_enum_examples", return_value=None
        ) as native:
            empty = generator.render_object_details(
                {"properties": {"enum": {"type": "string", "enum": []}}}, "example"
            )
            absent = generator.render_object_details(
                {"properties": {"enum": {"type": "string"}}}, "example"
            )
        self.assertIn("Must be one of: .", empty)
        self.assertNotIn("Must be one of:", absent)
        native.assert_called_once_with([])
