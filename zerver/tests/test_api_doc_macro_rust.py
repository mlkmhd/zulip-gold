from unittest import TestCase
from unittest.mock import call, patch

from markdown import Markdown

from zerver.lib.markdown import api_arguments_table_generator as api


class APIDocMacroRustTest(TestCase):
    def test_duplicate_directives_keep_wrapper_text_and_order(self) -> None:
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        line = "<p>{generate_api_arguments_table|A|/demo:get}</p>"
        source = [line, "آزمایشی", line]
        with (
            patch.object(api, "get_openapi_parameters", return_value=[]),
            patch.object(api, "get_parameters_description", return_value="") as source_description,
        ):
            result = renderer.run(source)
        empty = "This endpoint does not accept any parameters."
        self.assertEqual(result, ["<p>", empty, "</p>", "آزمایشی", "<p>", empty, "</p>"])
        self.assertEqual(source_description.call_args_list, [call("/demo", "get")] * 2)

    def test_native_registration_and_empty_list_identity(self) -> None:
        native = api._rust_expand_prepared_api_doc_macros
        if native is None:
            self.skipTest("requires the current native API docs macro driver")
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        lines: list[str] = []
        with patch.object(api, "_rust_expand_prepared_api_doc_macros", wraps=native) as run:
            self.assertIs(renderer.run(lines), lines)
        run.assert_called_once_with(lines, api.REGEXP, renderer)
        with self.assertRaises(ValueError):
            renderer.run(["{generate_api_arguments_table|A|missing_method}"])
