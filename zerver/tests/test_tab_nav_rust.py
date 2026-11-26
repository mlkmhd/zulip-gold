from unittest import TestCase
from unittest.mock import Mock, patch

from markdown import Markdown

from zerver.lib.markdown import tabbed_sections as tabs


class TabNavRustTest(TestCase):
    def test_nav_output_and_first_active(self) -> None:
        renderer = tabs.TabbedSectionsPreprocessor(Markdown(), {})
        section = {"tabs": [{"tab_key": "ios"}, {"tab_key": "python"}, {"tab_key": "ios"}]}
        rendered = renderer.generate_nav_bar(section)
        self.assertEqual(rendered.count('class="active"'), 1)
        self.assertIn('class="active" data-tab-key="ios"', rendered)
        self.assertIn('class="" data-tab-key="ios"', rendered)
        self.assertLess(rendered.index("iOS</li>"), rendered.index("Python</li>"))
        with patch.object(tabs, "_rust_build_prepared_tab_nav", None):
            self.assertEqual(renderer.generate_nav_bar(section), rendered)
        self.assertEqual(renderer.generate_nav_bar({"tabs": []}), '<ul class="nav">\n\n</ul>')

    def test_errors_follow_each_template_and_discard_later_tabs(self) -> None:
        renderer = tabs.TabbedSectionsPreprocessor(Markdown(), {})
        template = Mock(format=Mock(return_value="selected"))
        nav = Mock(format=Mock(return_value="navigation"))
        section = {"tabs": [{"tab_key": "ios"}, {}, {"tab_key": "python"}]}
        with (
            patch.object(tabs, "NAV_LIST_ITEM_TEMPLATE", template),
            patch.object(tabs, "NAV_BAR_TEMPLATE", nav),
            self.assertRaisesRegex(ValueError, "^Tab 'None' is not present"),
        ):
            renderer.generate_nav_bar(section)
        template.format.assert_called_once_with(class_="active", data_tab_key="ios", label="iOS")
        nav.format.assert_not_called()

    def test_actual_preprocessor_dispatches_and_uses_current_labels(self) -> None:
        native = tabs._rust_build_prepared_tab_nav
        if native is None:
            self.skipTest("requires the current native tab navigation builder")
        parser = tabs.TabbedSectionsPreprocessor(Markdown(), {})
        source = ["{start_tabs}", "{tab|ios}", "Step 1", "{end_tabs}"]
        with (
            patch.dict(tabs.TAB_SECTION_LABELS, ios="راهنمای آیفون"),
            patch.object(tabs, "_rust_build_prepared_tab_nav", wraps=native) as build,
        ):
            result = parser.run(source)
        build.assert_called_once()
        self.assertIn("راهنمای آیفون</li>", result[0])
