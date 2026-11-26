from unittest import TestCase
from unittest.mock import patch

from markdown import Markdown

from zerver.lib.markdown import api_arguments_table_generator as api
from zerver.openapi.openapi import Parameter


class APIDocDefaultsRustTest(TestCase):
    def fixture(self) -> Parameter:
        return Parameter(
            kind="query",
            name="sample",
            description="Synthetic note",
            json_encoded=False,
            value_schema={"type": "string", "enum": [None, False, "🌱"], "default": "آزمون"},
            example="sample",
            required=False,
            deprecated=False,
        )

    def test_nested_unicode_and_null_defaults(self) -> None:
        parameter = self.fixture()
        text = api._decorate_parameter_description(parameter.description, parameter)
        self.assertIn(r'<code>"\ud83c\udf31"</code>', text)
        self.assertIn("<code>null</code>, <code>false</code>", text)
        self.assertIn("Defaults to", text)
        parameter.value_schema = {"default": None, "enum": []}
        self.assertEqual(
            api._decorate_parameter_description("note", parameter), "note\nMust be one of: . "
        )

    def test_actual_registered_parameter_renderer_keeps_previous_html(self) -> None:
        native = api._rust_decorate_prepared_api_description
        if native is None:
            self.skipTest("requires current native API docs description helper")
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        param = self.fixture()
        with patch.object(api, "_rust_decorate_prepared_api_description", None):
            expected = renderer.render_parameters([param])
        with patch.object(api, "_rust_decorate_prepared_api_description", wraps=native) as describe:
            self.assertEqual(renderer.render_parameters([param]), expected)
        describe.assert_called_once()
