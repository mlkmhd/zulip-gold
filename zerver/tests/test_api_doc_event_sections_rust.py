from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from markdown import Markdown

from zerver.lib.markdown import api_return_values_table_generator as app


def fixture_event(event: str, op: str | None) -> dict[str, object]:
    props: dict[str, dict[str, object]] = {
        "type": {"type": "string", "enum": [event], "description": "Event label."},
        "id": {"type": "integer", "description": "شمارۀ رویداد."},
    }
    if op is not None:
        props["op"] = {"type": "string", "enum": [op]}
    return {
        "type": "object",
        "description": f"Description for {event}.",
        "properties": props,
        "example": {"z": "👩‍💻", "a": 17},
    }


class APIDocEventSectionsRustTest(TestCase):
    def test_real_rendering_preserves_event_details_and_the_sorted_index(self) -> None:
        runner = app.APIReturnValuesTablePreprocessor(Markdown(), {})
        schema = {
            "oneOf": [
                fixture_event("zeta", "remove"),
                fixture_event("alpha", None),
                fixture_event("zeta", "add"),
            ]
        }
        expected_in = deepcopy(schema)
        with patch.object(app, "_rust_render_prepared_api_event_sections", None):
            expected = runner.render_events(expected_in)
        current_in = deepcopy(schema)
        actual = runner.render_events(current_in)
        self.assertEqual(actual, expected)
        self.assertEqual(current_in, expected_in)
        index = actual[0]
        self.assertLess(index.index("#alpha"), index.index("#zeta-add"))
        self.assertLess(index.index("#zeta-add"), index.index("#zeta-remove"))
        joined = "\n".join(actual[1:])
        self.assertLess(joined.index('id="zeta-remove"'), joined.index('id="alpha"'))
        self.assertIn('"a": 17', joined)
        self.assertIn("شمارۀ رویداد", joined)

    def test_original_empty_op_error_does_not_render_any_event(self) -> None:
        runner = app.APIReturnValuesTablePreprocessor(Markdown(), {})
        schema: dict[str, object] = {"oneOf": [{"properties": {"op": {"enum": []}}}]}
        with (
            patch.object(runner, "generate_event_strings") as render,
            self.assertRaises(IndexError),
        ):
            runner.render_events(schema)
        render.assert_not_called()
        self.assertEqual(schema, {"oneOf": [{"properties": {}}]})

    def test_current_extension_is_used_without_mutating_shared_openapi_schema(self) -> None:
        native = app._rust_render_prepared_api_event_sections
        if native is None:
            self.skipTest("requires the current native event reference driver")
        shared = {
            "events": {
                "type": "array",
                "description": "Events",
                "items": {"oneOf": [fixture_event("sample", "update")]},
            }
        }
        original = deepcopy(shared)
        runner = app.APIReturnValuesTablePreprocessor(Markdown(), {})
        with (
            patch.object(app, "get_openapi_return_values", return_value=shared),
            patch.object(app, "_rust_render_prepared_api_event_sections", wraps=native) as render,
        ):
            output = runner.run(["{generate_return_values_table| events | /events:get}"])
        self.assertEqual(shared, original)
        self.assertEqual(render.call_count, 1)
        self.assertIn('href="#sample-update"', "\n".join(output))
