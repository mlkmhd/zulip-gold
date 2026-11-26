from unittest import TestCase
from unittest.mock import patch

from markdown import Markdown

from zerver.lib.markdown import api_arguments_table_generator as api


class APIDocPropertyRowRustTest(TestCase):
    def test_nested_request_docs_keep_the_original_html_and_types(self) -> None:
        runner = api.APIArgumentsTablePreprocessor(Markdown(), {})
        schema = {
            "required": ["public"],
            "properties": {
                "public": {
                    "type": "integer",
                    "description": "**public** range",
                    "default": 0,
                    "enum": [0, 1],
                    "example": "نمونه <&>",
                },
                "note": {"type": "string", "example": None},
                "internal": {"type": "boolean", "readOnly": True},
            },
        }
        with patch.object(api, "_rust_render_prepared_api_property_row", None):
            reference = runner.render_object_details(schema, "request")
        value = runner.render_object_details(schema, "request")
        self.assertEqual(value, reference)
        self.assertIn("<strong>public</strong>", value)
        self.assertIn("required</span>", value)
        self.assertIn("optional</span>", value)
        self.assertIn("Defaults to", value)
        self.assertIn("Must be one of", value)
        self.assertIn("&lt;&amp;&gt;", value)
        self.assertIn(">null<", value)
        self.assertNotIn("internal", value)

    def test_empty_required_list_and_malformed_membership_remain_distinct(self) -> None:
        runner = api.APIArgumentsTablePreprocessor(Markdown(), {})
        props = {"field": {"type": "string"}}
        self.assertIn(
            "optional</span>",
            runner.render_object_details({"properties": props, "required": []}, "x"),
        )
        with self.assertRaisesRegex(TypeError, "'NoneType' is not iterable"):
            runner.render_object_details({"properties": props, "required": None}, "x")

    def test_current_native_runs_only_on_selected_write_properties(self) -> None:
        native = api._rust_render_prepared_api_property_row
        if native is None:
            self.skipTest("requires the current native property row renderer")
        runner = api.APIArgumentsTablePreprocessor(Markdown(), {})
        fields = {"visible": {"type": "string"}, "hidden": {"readOnly": True}}
        with patch.object(api, "_rust_render_prepared_api_property_row", wraps=native) as render:
            runner.render_object_details({"properties": fields}, "request")
        self.assertEqual(render.call_count, 1)
        self.assertIs(render.call_args.args[1], fields)
        self.assertEqual(render.call_args.args[2], "visible")
