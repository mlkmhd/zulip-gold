from unittest import TestCase
from unittest.mock import patch

from markdown import Markdown

from zerver.lib.markdown import api_return_values_table_generator as app


class APIReturnDescriptionRustTest(TestCase):
    def test_named_keys_and_original_multiline_rule(self) -> None:
        runner = app.APIReturnValuesTablePreprocessor(Markdown(), {})
        self.assertEqual(
            runner.render_desc("شناسه: شرح\nNote: body", 2, "integer"),
            '  * شناسه: <span class="api-field-type">integer</span>\n\n      شرح\n      Note: body',
        )
        self.assertEqual(
            runner.render_desc("first\nNote: body", 0, "integer"), "* first\n    Note: body"
        )
        self.assertEqual(
            runner.render_desc("value", -5, "integer", ""),
            '* ``: <span class="api-field-type">integer</span>\n\nvalue',
        )

    def test_unusual_strings_keep_python_behavior(self) -> None:
        runner = app.APIReturnValuesTablePreprocessor(Markdown(), {})
        self.assertEqual(runner.render_desc("test\ud800", 0, "integer"), "* test\ud800")
        with patch.object(app, "_rust_render_prepared_api_return_description", None):
            expected = runner.render_desc("name: data", 4097, "integer")
        self.assertEqual(runner.render_desc("name: data", 4097, "integer"), expected)

    def test_actual_table_uses_the_complete_renderer(self) -> None:
        native = app._rust_render_prepared_api_return_description
        if native is None:
            self.skipTest("requires the current native description renderer")
        runner = app.APIReturnValuesTablePreprocessor(Markdown(), {})
        fields = {"size": {"type": "integer", "description": "Stored size."}}
        with patch.object(
            app, "_rust_render_prepared_api_return_description", wraps=native
        ) as render:
            actual = runner.render_table(fields, 4)
        render.assert_called_once_with("Stored size.", 4, "integer", "size")
        self.assertEqual(
            actual,
            ['    * `size`: <span class="api-field-type">integer</span>\n\n        Stored size.'],
        )
