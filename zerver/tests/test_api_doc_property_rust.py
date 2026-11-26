from unittest import TestCase
from unittest.mock import patch

from markdown import Markdown

from zerver.lib.markdown import api_arguments_table_generator as api


class APIDocPropertyRustTest(TestCase):
    def test_property_precedence_and_exact_error(self) -> None:
        runner = api.APIArgumentsTablePreprocessor(Markdown(), {})
        inner: dict[str, dict[str, str]] = {"properties": {}}
        property_ = {"items": inner, "oneOf": []}
        with patch.object(runner, "render_object_details", return_value="details") as render:
            result = api._render_api_property_detail(runner, {"item": property_}, "item", "array")
        self.assertEqual(result, "details")
        render.assert_called_once_with(inner, "item")
        with self.assertRaisesRegex(TypeError, "'NoneType' is not iterable"):
            api._render_api_property_detail(runner, {"x": {"items": None}}, "x", "string")

    def test_current_extension_uses_original_callback_in_actual_renderer(self) -> None:
        native = api._rust_choose_prepared_api_property_detail
        if native is None:
            self.skipTest("requires the current native property detail selector")
        runner = api.APIArgumentsTablePreprocessor(Markdown(), {})
        schema = {
            "properties": {
                "sample": {"type": "object", "properties": {"field": {"type": "string"}}}
            }
        }
        with patch.object(api, "_rust_choose_prepared_api_property_detail", None):
            expected = runner.render_object_details(schema, "request")
        with patch.object(api, "_rust_choose_prepared_api_property_detail", wraps=native) as choose:
            self.assertEqual(runner.render_object_details(schema, "request"), expected)
        self.assertGreaterEqual(choose.call_count, 2)
