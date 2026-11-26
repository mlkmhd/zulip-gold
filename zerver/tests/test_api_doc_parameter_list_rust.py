from unittest import TestCase
from unittest.mock import patch

from markdown import Markdown

from zerver.lib.markdown import api_arguments_table_generator as api
from zerver.openapi.openapi import Parameter


class APIDocParameterListRustTest(TestCase):
    def parameter(self, name: str, flagged: bool) -> Parameter:
        return Parameter(
            kind="query",
            name=name,
            description="**Deprecated**" if flagged else "note",
            json_encoded=False,
            value_schema={"type": "string"},
            example="<sample>",
            required=False,
            deprecated=flagged,
        )

    def test_stable_sort_and_shared_markdown_engine(self) -> None:
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        entries = [
            self.parameter("one", True),
            self.parameter("two", False),
            self.parameter("three", True),
            self.parameter("four", False),
        ]
        with patch.object(
            renderer, "_render_parameter", side_effect=lambda p, md: p.name
        ) as render:
            self.assertEqual(renderer.render_parameters(entries), ["two", "four", "one", "three"])
        engines = [row.args[1] for row in render.call_args_list]
        self.assertTrue(all(engine is engines[0] for engine in engines))
        self.assertEqual(entries[0].name, "one")

    def test_actual_markdown_html_and_native_dispatch(self) -> None:
        native = api._rust_render_prepared_api_parameter_list
        if native is None:
            self.skipTest("requires the current native parameter list")
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        entries = [self.parameter("old", True), self.parameter("new", False)]
        with patch.object(api, "_rust_render_prepared_api_parameter_list", None):
            expected = renderer.render_parameters(entries)
        with patch.object(api, "_rust_render_prepared_api_parameter_list", wraps=native) as run:
            self.assertEqual(renderer.render_parameters(entries), expected)
        run.assert_called_once()
