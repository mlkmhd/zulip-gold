import os
from collections.abc import Callable
from contextlib import ExitStack
from importlib import import_module
from unittest import TestCase
from unittest.mock import Mock, patch

from markdown import Markdown

from zerver.lib.markdown import api_arguments_table_generator as arguments
from zerver.lib.markdown import api_return_values_table_generator as returns
from zerver.lib.native_backend import supports_default_rust_backend
from zerver.lib.partial import partial
from zerver.openapi.openapi import Parameter

FIXTURE_ROUTE = "/synthetic-docs"
FIXTURE_PAGE = """# Synthetic reference

Before the generated sections.

{generate_api_arguments_table| fixture | /synthetic-docs:get}

{generate_return_values_table| fixture | /synthetic-docs:get}

After the generated sections.
"""

TREE_HOOK = "_rust_render_api_doc_type_tree"
TREE_FLAG = "ZULIP_RUST_API_DOC_TYPE_TREE"

NATIVE_HOOKS = (
    (arguments, "_rust_join_prepared_doc_type_labels", "ZULIP_RUST_API_TYPE_DISPLAY", 1),
    (arguments, "_rust_wrap_prepared_doc_array_label", "ZULIP_RUST_API_TYPE_DISPLAY", 2),
    (arguments, "_rust_append_doc_nullable_label", "ZULIP_RUST_API_TYPE_DISPLAY", 2),
    (arguments, "_rust_join_prepared_api_enum_examples", "ZULIP_RUST_API_ENUM_DISPLAY", 1),
    (returns, "_rust_indent_prepared_api_return_description", "ZULIP_RUST_API_RETURN_INDENT", 3),
)


def require_native_text(native: Callable[..., object], *args: object) -> str:
    result = native(*args)
    assert type(result) is str, "the prepared documentation values should not be declined"
    return result


class APIDocsRenderFlowRustTest(TestCase):
    @patch.object(returns, "_rust_render_prepared_api_return_description", None)
    def render_synthetic_reference(self) -> str:
        # This fixture probes the compatibility helpers individually. The full
        # native return description is exercised by its own table/Markdown test.
        parameters = [
            Parameter(
                kind="query",
                name="kind",
                description="Select the display kind.",
                json_encoded=True,
                value_schema={
                    "type": "string",
                    "nullable": True,
                    "enum": ["compact", "فارسی"],
                },
                example="<&>",
                required=True,
                deprecated=False,
            ),
            Parameter(
                kind="query",
                name="formats",
                description="Choose the display formats.",
                json_encoded=True,
                value_schema={
                    "oneOf": [{"type": "integer"}, {"type": "array", "items": {"type": "string"}}]
                },
                example=["compact"],
                required=False,
                deprecated=False,
            ),
        ]
        fields = {
            "result": {"type": "string", "description": "Omitted result metadata."},
            "receipt": {
                "type": "object",
                "description": "First paragraph.\n\nSecond paragraph.",
                "properties": {
                    "count": {"type": "integer", "description": "Number of values."},
                    "labels": {
                        "type": "array",
                        "items": {"type": "string", "nullable": True},
                        "description": "First line.\nSecond line: فارسی.",
                    },
                },
            },
        }

        engine = Markdown(extensions=[arguments.makeExtension(), returns.makeExtension()])
        self.assertIsInstance(
            engine.preprocessors["generate_api_arguments"],
            arguments.APIArgumentsTablePreprocessor,
        )
        self.assertIsInstance(
            engine.preprocessors["generate_return_values"],
            returns.APIReturnValuesTablePreprocessor,
        )
        with (
            patch.object(
                arguments, "get_openapi_parameters", return_value=parameters
            ) as arg_lookup,
            patch.object(
                returns, "get_openapi_return_values", return_value=fields
            ) as return_lookup,
        ):
            html = engine.convert(FIXTURE_PAGE)
        arg_lookup.assert_called_once_with(FIXTURE_ROUTE, "get")
        return_lookup.assert_called_once_with(FIXTURE_ROUTE, "get")
        return html

    def render_python_baseline(self) -> str:
        with ExitStack() as stack:
            stack.enter_context(patch.object(arguments, TREE_HOOK, None))
            for module, hook, _, _ in NATIVE_HOOKS:
                stack.enter_context(patch.object(module, hook, None))
            return self.render_synthetic_reference()

    def assert_expected_document(self, html: str) -> None:
        self.assertIn('id="parameter-kind"', html)
        self.assertIn('id="parameter-formats"', html)
        self.assertIn("&lt;&amp;&gt;", html)
        self.assertIn('Must be one of: <code>"compact"</code>', html)
        self.assertIn("string | null", html)
        self.assertIn("integer | (string)[]", html)
        self.assertIn("<h4>Return values</h4>", html)
        self.assertIn("Second paragraph.", html)
        self.assertIn("Second line: فارسی.", html)
        self.assertNotIn("Omitted result metadata.", html)
        self.assertNotIn("generate_api_arguments_table", html)
        self.assertNotIn("generate_return_values_table", html)
        self.assertLess(
            html.index("Before the generated sections."),
            html.index('id="parameter-kind"'),
        )
        self.assertLess(
            html.index("Second line: فارسی."),
            html.index("After the generated sections."),
        )

    def test_registered_renderers_match_python_with_selected_native_helpers(self) -> None:
        expected = self.render_python_baseline()
        self.assert_expected_document(expected)

        try:
            backend = import_module("_zulip_backend")
        except ImportError:
            backend = None
        with ExitStack() as stack:
            tree_enabled = os.environ.get(TREE_FLAG) == "1" or (
                supports_default_rust_backend()
                and getattr(backend, "API_DOC_TYPE_TREE_API", None) == 1
            )
            tree = getattr(arguments, TREE_HOOK)
            self.assertEqual(callable(tree), tree_enabled)
            tree_spy = (
                stack.enter_context(
                    patch.object(
                        arguments, TREE_HOOK, side_effect=partial(require_native_text, tree)
                    )
                )
                if tree_enabled
                else None
            )
            spies: list[tuple[Mock, int]] = []
            for module, hook, flag, expected_calls in NATIVE_HOOKS:
                native = getattr(module, hook)
                contract = flag.removeprefix("ZULIP_RUST_") + "_API"
                default_enabled = (
                    supports_default_rust_backend() and getattr(backend, contract, None) == 1
                )
                explicitly_enabled = os.environ.get(flag) == "1"
                if default_enabled or explicitly_enabled:
                    self.assertTrue(callable(native), (flag, hook))
                    spy = stack.enter_context(
                        patch.object(module, hook, side_effect=partial(require_native_text, native))
                    )
                    if tree_enabled and flag == "ZULIP_RUST_API_TYPE_DISPLAY":
                        expected_calls = 0
                    spies.append((spy, expected_calls))
                else:
                    self.assertIsNone(native, (flag, hook))
            self.assertEqual(self.render_synthetic_reference(), expected)
            if tree_spy is not None:
                self.assertGreater(tree_spy.call_count, 0)
            for spy, expected_calls in spies:
                self.assertEqual(spy.call_count, expected_calls)

    def test_same_document_for_partial_exports_and_native_declines(self) -> None:
        expected = self.render_python_baseline()
        for missing in (None, "_rust_wrap_prepared_doc_array_label"):
            with self.subTest(missing=missing), ExitStack() as stack:
                tree = stack.enter_context(patch.object(arguments, TREE_HOOK, return_value=None))
                declines: list[tuple[Mock, int]] = []
                for module, hook, _, expected_calls in NATIVE_HOOKS:
                    if hook == missing:
                        stack.enter_context(patch.object(module, hook, None))
                    else:
                        decline = stack.enter_context(patch.object(module, hook, return_value=None))
                        declines.append((decline, expected_calls))
                self.assertEqual(self.render_synthetic_reference(), expected)
                self.assertGreater(tree.call_count, 0)
                for decline, expected_calls in declines:
                    self.assertEqual(decline.call_count, expected_calls)
