from typing import Literal
from unittest import TestCase
from unittest.mock import patch

from markdown import Markdown

from zerver.lib.markdown import api_arguments_table_generator as api
from zerver.openapi.openapi import Parameter


class APIDocBadgesRustTest(TestCase):
    def make_param(
        self, kind: Literal["query", "path", "formData"], required: bool, deprecated: bool
    ) -> Parameter:
        return Parameter(
            kind=kind,
            name="sample",
            description="**Deprecated**" if deprecated else "note",
            json_encoded=False,
            value_schema={"type": "string"},
            example="x",
            required=required,
            deprecated=deprecated,
        )

    def test_path_assertion_order_and_full_description(self) -> None:
        path = self.make_param("path", False, True)
        with (
            patch.object(api, "check_deprecated_consistency") as validate,
            self.assertRaises(AssertionError) as error,
        ):
            api._prepare_parameter_badges(path, path.description)
        self.assertEqual(error.exception.args, ())
        validate.assert_not_called()
        self.assertIn(
            "required in path",
            api._prepare_parameter_badges(self.make_param("path", True, False), "note")[0],
        )
        with self.assertRaisesRegex(AssertionError, "Missing"):
            api._prepare_parameter_badges(self.make_param("query", False, False), "**Deprecated**")

    def test_actual_parameter_renderer_matches_original_output(self) -> None:
        native = api._rust_prepare_api_doc_badges
        if native is None:
            self.skipTest("requires the current native badge helper")
        renderer = api.APIArgumentsTablePreprocessor(Markdown(), {})
        params = [self.make_param("query", False, False), self.make_param("path", True, True)]
        with patch.object(api, "_rust_prepare_api_doc_badges", None):
            original = renderer.render_parameters(params)
        with patch.object(api, "_rust_prepare_api_doc_badges", wraps=native) as prepare:
            self.assertEqual(renderer.render_parameters(params), original)
        self.assertEqual(prepare.call_count, 2)
