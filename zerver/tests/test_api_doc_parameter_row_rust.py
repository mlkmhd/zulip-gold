from unittest import TestCase
from unittest.mock import patch

from markdown import Markdown

from zerver.lib.markdown import api_arguments_table_generator as api
from zerver.openapi.openapi import Parameter


class APIDocParameterRowRustTest(TestCase):
    def test_markdown_example_escaping_and_nested_fields(self) -> None:
        row = Parameter(
            kind="query",
            name="my_rows",
            description="A **grammar**.",
            json_encoded=True,
            value_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "private": {"readOnly": True}},
                    "required": ["name"],
                },
            },
            example=[{"name": "<tag>&"}],
            required=True,
            deprecated=False,
        )
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        with patch.object(api, "_rust_render_prepared_api_parameter_row", None):
            reference = renderer.render_parameters([row])
        actual = renderer.render_parameters([row])
        self.assertEqual(actual, reference)
        self.assertIn("<strong>grammar</strong>", actual[0])
        self.assertIn("&lt;tag&gt;&amp;", actual[0])
        self.assertIn("required", actual[0])
        self.assertNotIn("private", actual[0])

    def test_original_error_order(self) -> None:
        row = Parameter(
            kind="path",
            name="sample",
            description="Sample",
            json_encoded=False,
            value_schema={"type": "string"},
            example=object(),
            required=False,
            deprecated=False,
        )
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        with self.assertRaisesRegex(TypeError, "not JSON serializable"):
            renderer.render_parameters([row])

    def test_current_renderer_keeps_exact_application_callbacks(self) -> None:
        native = api._rust_render_prepared_api_parameter_row
        if native is None:
            self.skipTest("requires the current native API documentation row renderer")
        row = Parameter(
            kind="query",
            name="topic",
            description="Topic",
            json_encoded=False,
            value_schema={"type": "string"},
            example="hello",
            required=False,
            deprecated=False,
        )
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        with patch.object(api, "_rust_render_prepared_api_parameter_row", wraps=native) as render:
            renderer.render_parameters([row])
        self.assertEqual(render.call_count, 1)
        self.assertIs(render.call_args.args[0], row)
