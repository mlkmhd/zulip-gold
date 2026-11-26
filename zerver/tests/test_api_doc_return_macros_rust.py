from copy import deepcopy
from unittest import TestCase
from unittest.mock import call, patch

from markdown import Markdown

from zerver.lib.markdown import api_return_values_table_generator as returns


class APIDocReturnMacrosRustTest(TestCase):
    def test_regular_macros_keep_affixes_order_and_empty_output(self) -> None:
        marker = "{generate_return_values_table| read | /synthetic:get}"
        empty = "{generate_return_values_table| empty | /synthetic/empty:get}"
        expected_fields = {"total": {"type": "integer", "description": "A **count**."}}
        calls = [
            call("/synthetic", "get"),
            call("/synthetic/empty", "get"),
            call("/synthetic", "get"),
        ]
        text = f"Before {marker} after\n\n{empty}\n\n{marker}"
        with (
            patch.object(
                returns,
                "get_openapi_return_values",
                side_effect=[expected_fields, {}, expected_fields],
            ) as lookup,
            patch.object(returns, "_rust_expand_prepared_api_return_macros", None),
        ):
            reference = Markdown(extensions=[returns.makeExtension()]).convert(text)
            self.assertEqual(lookup.call_args_list, calls)
        with patch.object(
            returns, "get_openapi_return_values", side_effect=[expected_fields, {}, expected_fields]
        ) as lookup:
            actual = Markdown(extensions=[returns.makeExtension()]).convert(text)
            self.assertEqual(lookup.call_args_list, calls)
        self.assertEqual(actual, reference)
        self.assertEqual(actual.count("Return values"), 2)
        self.assertIn("Before", actual)
        self.assertIn("after", actual)
        self.assertNotIn("generate_return_values_table", actual)

    def test_event_schema_is_copied_before_the_item_is_removed(self) -> None:
        rows = {"events": {"type": "array", "items": {"oneOf": []}, "description": "Events"}}
        frozen = deepcopy(rows)
        runner = returns.APIReturnValuesTablePreprocessor(Markdown(), {})
        with (
            patch.object(returns, "get_openapi_return_values", return_value=rows) as lookup,
            patch.object(runner, "render_table", return_value=["event table"]) as table,
            patch.object(runner, "render_events", return_value=["event detail"]) as events,
        ):
            actual = runner.run(["{generate_return_values_table| events | /events:get}"])
        lookup.assert_called_once_with("/events", "get")
        events.assert_called_once_with({"oneOf": []})
        table.assert_called_once_with({"events": {"type": "array", "description": "Events"}}, 0)
        self.assertEqual(rows, frozen)
        self.assertEqual(
            actual,
            [
                "",
                "#### Return values",
                "event table",
                "\n\n## Events by `type`\n\n",
                "event detail",
                "",
            ],
        )

    def test_malformed_macro_stops_before_the_next_lookup(self) -> None:
        runner = returns.APIReturnValuesTablePreprocessor(Markdown(), {})
        with (
            patch.object(returns, "get_openapi_return_values") as lookup,
            self.assertRaisesRegex(ValueError, "not enough values to unpack"),
        ):
            runner.run(
                [
                    "{generate_return_values_table| malformed | ignored}",
                    "{generate_return_values_table| valid | /synthetic:get}",
                ]
            )
        lookup.assert_not_called()

    def test_current_extension_dispatches_the_registered_return_preprocessor(self) -> None:
        native = returns._rust_expand_prepared_api_return_macros
        if native is None:
            self.skipTest("requires the current native API documentation macro driver")
        with (
            patch.object(returns, "get_openapi_return_values", return_value={}),
            patch.object(
                returns, "_rust_expand_prepared_api_return_macros", wraps=native
            ) as expand,
        ):
            Markdown(extensions=[returns.makeExtension()]).convert(
                "{generate_return_values_table| fixture | /synthetic:get}"
            )
        self.assertEqual(expand.call_count, 1)
