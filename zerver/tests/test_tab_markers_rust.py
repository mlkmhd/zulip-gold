from typing import cast
from unittest import TestCase
from unittest.mock import patch

from markdown import Markdown

from zerver.lib.markdown import tabbed_sections as tabs


class TabMarkersRustTest(TestCase):
    def test_multiple_starts_single_lf_and_first_end(self) -> None:
        source = [
            "前",
            "{tab|\\ud800i\n} \n",
            "{tab|ایران}\n",
            "{start_tabs}",
            "{tab|a}x}",
            "{start_tabs}\n",
            "{tab|🌐\n\ud800}",
            "{end_tabs}\r\n",
            "{end_tabs}\n",
            "{tab|ignored}",
        ]
        parsed = tabs.parse_tabs(source)
        assert parsed is not None
        self.assertEqual(list(parsed), ["tabs", "start_tabs_index", "end_tabs_index"])
        self.assertEqual(
            parsed,
            {
                "tabs": [{"start": 2, "tab_key": "ایران"}, {"start": 6, "tab_key": "🌐\n\ud800"}],
                "start_tabs_index": 5,
                "end_tabs_index": 8,
            },
        )
        with patch.object(tabs, "_rust_parse_prepared_tab_markers", None):
            self.assertEqual(tabs.parse_tabs(source), parsed)

    def test_invalid_row_and_unparsed_suffix(self) -> None:
        bad = cast("list[str]", ["{start_tabs}", 1])
        with self.assertRaisesRegex(TypeError, "expected string or bytes-like object"):
            tabs.parse_tabs(bad)
        after_end = cast("list[str]", ["{end_tabs}", 1])
        self.assertEqual(tabs.parse_tabs(after_end), {"end_tabs_index": 0})

    def test_real_render_selects_rust_parser_and_preserves_html(self) -> None:
        parse = tabs._rust_parse_prepared_tab_markers
        if parse is None:
            self.skipTest("requires the current native tab marker parser")
        source = ["intro", "{start_tabs}", "{tab|python}", "Some **code**", "{end_tabs}", "foot"]
        renderer = tabs.TabbedSectionsPreprocessor(Markdown(), {})
        with patch.object(tabs, "_rust_parse_prepared_tab_markers", None):
            expected = renderer.run(source)
        with patch.object(tabs, "_rust_parse_prepared_tab_markers", wraps=parse) as native:
            self.assertEqual(renderer.run(source), expected)
        self.assertEqual(native.call_count, 2)
        self.assertIn("Python</li>", expected[1])
