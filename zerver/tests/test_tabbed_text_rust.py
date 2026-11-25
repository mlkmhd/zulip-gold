from importlib import import_module
from typing import cast
from unittest import TestCase
from unittest.mock import Mock, patch

from markdown import Markdown
from typing_extensions import override

from zerver.lib.markdown import tabbed_sections as tabs


class TabbedTextRustTest(TestCase):
    def test_native_prepared_lf_and_python_whitespace(self) -> None:
        try:
            module = import_module("_zulip_backend")
            normalize = module.normalize_prepared_tabbed_lines
            join = module.join_prepared_tabbed_blocks
        except (ImportError, AttributeError):
            self.skipTest("Rust documentation tab text helpers are not available")

        unchanged = "label:آ🙂\u200b".removeprefix("label:")
        self.assertIs(normalize([unchanged]), unchanged)
        self.assertIs(join([unchanged]), unchanged)
        self.assertEqual(
            normalize(["\t\r\x1c\u2009", "one\u00a0two", "\x1f \u3000"]), "one\u00a0two"
        )
        self.assertEqual(normalize(["", "\x85", ""]), "")
        self.assertEqual(normalize(["\u200b", " value ", "\ufeff"]), "\u200b\n value \n\ufeff")
        self.assertEqual(join(["a\r", "b\n"]), "a\r\nb\n")
        self.assertEqual(join([]), "")
        if getattr(module, "TABBED_TEXT_API", None) == 1:
            surrogate_text = "".join(map(chr, range(0xD800, 0xE000))) + "🙂"
            self.assertIs(normalize([surrogate_text]), surrogate_text)
            values = ["\xa0\u2002", surrogate_text, "\udfff\u3000"]
            self.assertEqual(normalize(values), "\n".join(values).strip())
            self.assertEqual(join(values), "\n".join(values))
        self.assertIsNone(normalize(("\t",)))
        self.assertIsNone(join(["x", 1]))

    def test_real_preprocessor_selects_and_formats_tabs_in_python(self) -> None:
        normalizer = tabs._rust_normalize_prepared_tabbed_lines
        joiner = tabs._rust_join_prepared_tabbed_blocks
        normalize = Mock(wraps=normalizer) if normalizer is not None else None
        join = Mock(wraps=joiner) if joiner is not None else None
        first = ["  \x1d", "  First *text* ", ""]
        second = ["\u2029 Second ", "line\u3000"]
        source = [
            "before",
            "{start_tabs}",
            "{tab|desktop-web}",
            *first,
            "{tab|ios}",
            *second,
            "{end_tabs}",
            "after",
        ]
        with (
            patch.object(tabs, "_rust_normalize_prepared_tabbed_lines", normalize),
            patch.object(tabs, "_rust_join_prepared_tabbed_blocks", join),
        ):
            result = tabs.TabbedSectionsPreprocessor(Markdown(), {}).run(source)
        prepared = [
            tabs.DIV_TAB_CONTENT_TEMPLATE.format(
                class_="active", data_tab_key="desktop-web", content="\nFirst *text*\n"
            ),
            tabs.DIV_TAB_CONTENT_TEMPLATE.format(
                class_="", data_tab_key="ios", content="\nSecond \nline\n"
            ),
        ]
        self.assertEqual(result[::2], ["before", "after"])
        self.assertIn("\n".join(prepared), result[1])
        self.assertIn("Desktop/Web</li>", result[1])
        self.assertIn("iOS</li>", result[1])
        with (
            patch.object(tabs, "_rust_normalize_prepared_tabbed_lines", None),
            patch.object(tabs, "_rust_join_prepared_tabbed_blocks", None),
        ):
            self.assertEqual(tabs.TabbedSectionsPreprocessor(Markdown(), {}).run(source), result)
        if normalize is not None:
            self.assertEqual(
                [call.args for call in normalize.call_args_list], [(first,), (second,)]
            )
        if join is not None:
            join.assert_called_once_with(prepared)

    def test_declines_keep_template_callback_and_python_string_semantics(self) -> None:
        calls: list[dict[str, object]] = []

        class Template(str):
            __slots__ = ()

            @override
            def format(self, *args: object, **kwargs: object) -> str:
                calls.append(kwargs)
                return cast("str", kwargs["content"])

        tab_section = {"tabs": [{"start": 0, "tab_key": "ios"}], "end_tabs_index": 2}
        lines = ["{tab|ios}", "\u202f\ud800\n\t", "{end_tabs}"]
        with (
            patch.object(tabs, "_rust_normalize_prepared_tabbed_lines", return_value=None) as strip,
            patch.object(tabs, "_rust_join_prepared_tabbed_blocks", return_value=None) as join,
        ):
            actual = tabs.generate_content_blocks(tab_section, lines, Template("unused"))
        self.assertEqual(actual, "\n\ud800\n")
        self.assertEqual(
            calls,
            [
                {
                    "class_": "active",
                    "data_tab_key": "ios",
                    "tab_label": "iOS",
                    "content": "\n\ud800\n",
                }
            ],
        )
        strip.assert_called_once_with([lines[1]])
        join.assert_called_once_with(["\n\ud800\n"])

    def test_original_missing_label_and_nonstring_errors(self) -> None:
        with self.assertRaises(KeyError) as key:
            tabs.generate_content_blocks(
                {"tabs": [{"start": 0, "tab_key": "unknown"}], "end_tabs_index": 2},
                ["menu", "one", "end"],
                "{content}",
            )
        self.assertEqual(key.exception.args, ("unknown",))
        lines = cast("list[str]", ["menu", 7, "end"])
        with self.assertRaisesRegex(TypeError, "sequence item 0: expected str instance, int found"):
            tabs.generate_content_blocks(
                {"tabs": [{"start": 0, "tab_key": "ios"}], "end_tabs_index": 2},
                lines,
                "{content}",
            )
