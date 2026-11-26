from unittest import TestCase
from unittest.mock import call, patch

from markdown import Markdown

from zerver.lib.markdown import api_arguments_table_generator as api


class APIDocOneOfRustTest(TestCase):
    def test_actual_markdown_and_property_precedence(self) -> None:
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        inner: dict[str, dict[str, dict[str, str]]] = {"properties": {"a": {}}}
        outer = {"properties": {"b": {}}, "description": "not selected"}
        schema = {"oneOf": [{"description": "**visible**"}, {}, {"items": inner}, outer]}
        with patch.object(
            renderer, "render_object_details", side_effect=["inside", "outside"]
        ) as detail:
            output = renderer.render_oneof_block(schema, "name")
        self.assertIn(
            "<li><p><strong>visible</strong></p></li><li>inside</li><li>outside</li>", output
        )
        self.assertEqual(
            detail.call_args_list, [call(inner, "name", True), call(outer, "name", True)]
        )

    def test_first_invalid_array_schema_raises_original_error(self) -> None:
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        with self.assertRaises(TypeError):
            renderer.render_oneof_block({"oneOf": [{"items": None, "properties": {}}]}, "name")

    def test_current_extension_matches_actual_markdown(self) -> None:
        native = api._rust_render_prepared_api_oneof
        if native is None:
            self.skipTest("requires current oneOf renderer")
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        schema = {"oneOf": [{"description": "*آزمون*"}, {"description": ""}]}
        with patch.object(api, "_rust_render_prepared_api_oneof", None):
            original = renderer.render_oneof_block(schema, "name")
        with patch.object(api, "_rust_render_prepared_api_oneof", wraps=native) as render:
            self.assertEqual(renderer.render_oneof_block(schema, "name"), original)
        render.assert_called_once()
