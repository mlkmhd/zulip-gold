from unittest import TestCase
from unittest.mock import Mock, patch

from markdown import Markdown

from zerver.lib.markdown import tabbed_sections as tabs


class TabContentRustTest(TestCase):
    def test_custom_template_and_prepared_content_match(self) -> None:
        lines = ["{tab|ios}", "\u2000one ", "{tab|python}", "\x1c\ud800two\u3000", "{end_tabs}"]
        section = {
            "tabs": [{"start": 0, "tab_key": "ios"}, {"start": 2, "tab_key": "python"}],
            "end_tabs_index": 4,
        }
        template = "{tab_label} ({data_tab_key}/{class_}):{content}"
        result = tabs.generate_content_blocks(section, lines, template)
        self.assertEqual(result, "iOS (ios/active):\none\n\nPython (python/):\n\ud800two\n")
        with patch.object(tabs, "_rust_build_prepared_tab_content", None):
            self.assertEqual(tabs.generate_content_blocks(section, lines, template), result)

    def test_stop_at_callback_failure_before_label_lookup(self) -> None:
        faulty = Mock(side_effect=ValueError("content normalization stopped"))
        with (
            patch.object(tabs, "_rust_normalize_prepared_tabbed_lines", faulty),
            self.assertRaisesRegex(ValueError, "^content normalization stopped$"),
        ):
            tabs.generate_content_blocks(
                {"tabs": [{"start": 0, "tab_key": "not-known"}], "end_tabs_index": 2},
                ["step", "text"],
                "{content}",
            )
        faulty.assert_called_once_with(["text"])

    def test_current_extension_is_used_by_the_full_renderer(self) -> None:
        native = tabs._rust_build_prepared_tab_content
        if native is None:
            self.skipTest("requires the current native tab content builder")
        parser = tabs.TabbedSectionsPreprocessor(Markdown(), {})
        source = ["{start_tabs}", "{tab|ios}", "Step 1", "{end_tabs}"]
        with patch.object(tabs, "_rust_build_prepared_tab_content", None):
            expected = parser.run(source)
        with patch.object(tabs, "_rust_build_prepared_tab_content", wraps=native) as render:
            self.assertEqual(parser.run(source), expected)
        render.assert_called_once()
        self.assertIn("\n\nStep 1\n\n", expected[0])
