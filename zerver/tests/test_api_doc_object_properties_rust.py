from unittest import TestCase
from unittest.mock import patch

from markdown import Markdown

from zerver.lib.markdown import api_arguments_table_generator as api


class APIDocObjectPropertiesRustTest(TestCase):
    def test_filter_order_and_single_markdown_engine(self) -> None:
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        schema = {"properties": {"a": {"readOnly": True}, "نام": {}, "x": {"readOnly": False}}}
        with patch.object(
            renderer,
            "_render_object_property",
            side_effect=lambda s, o, key, md: "row:" + key,
        ) as render:
            text = renderer.render_object_details(schema, "N", True)
        self.assertEqual(text, api.ONEOF_OBJECT_DETAILS_TEMPLATE.format(values="row:نام\nrow:x"))
        self.assertEqual([item.args[2] for item in render.call_args_list], ["نام", "x"])
        self.assertIs(render.call_args_list[0].args[3], render.call_args_list[1].args[3])

    def test_real_object_renderer_preserves_previous_html(self) -> None:
        native = api._rust_render_prepared_api_object_properties
        if native is None:
            self.skipTest("requires current native object property renderer")
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        schema = {
            "properties": {
                "text": {"type": "string", "description": "**note**", "example": "<sample>"},
                "ignored": {"type": "string", "readOnly": True},
            },
            "required": ["text"],
        }
        with patch.object(api, "_rust_render_prepared_api_object_properties", None):
            expected = renderer.render_object_details(schema, "request")
        with patch.object(
            api, "_rust_render_prepared_api_object_properties", wraps=native
        ) as render:
            self.assertEqual(renderer.render_object_details(schema, "request"), expected)
        self.assertIn("&lt;sample&gt;", expected)
        self.assertNotIn("ignored", expected)
        render.assert_called_once()
