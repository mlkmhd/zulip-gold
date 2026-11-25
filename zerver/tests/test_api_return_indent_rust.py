from importlib import import_module
from typing import SupportsIndex
from unittest import TestCase
from unittest.mock import Mock, patch

from markdown import Markdown
from typing_extensions import override

from zerver.lib.markdown import api_return_values_table_generator as returns


class APIReturnIndentRustTest(TestCase):
    def test_native_prepared_lf_replacement_and_noop_identity(self) -> None:
        try:
            module = import_module("_zulip_backend")
            native = module.indent_prepared_api_return_description
        except (AttributeError, ImportError):
            self.skipTest("Rust API return description indentation is not available")

        no_lf = "tag:آ🙂\r\u2028".removeprefix("tag:")
        with_lf = "tag:A\nB".removeprefix("tag:")
        self.assertIs(native(no_lf, "\n    "), no_lf)
        self.assertIs(native(with_lf, "\n"), with_lf)
        self.assertEqual(native("\r\nیک\u2028\n\n", "\n   "), "\r\n   یک\u2028\n   \n   ")
        if getattr(module, "API_RETURN_INDENT_API", None) == 1:
            text = "prefix\ud800\n\udfff\n\ud800\udfff🙂"
            self.assertEqual(native(text, "\n "), text.replace("\n", "\n "))
            self.assertIs(native(text, "\n"), text)
        self.assertIsNone(native("a\nb", "\n\t"))
        self.assertIsNone(native("a\nb", " "))
        self.assertIsNone(native("\n" * 1024, "\n" + " " * 8192))

    def test_real_table_and_key_description_paths_are_unchanged(self) -> None:
        native = returns._rust_indent_prepared_api_return_description
        spy = Mock(wraps=native) if native is not None else None
        docs = returns.APIReturnValuesTablePreprocessor(Markdown(), {})
        desc = "First\r\nSecond\u2028same\n  Third"
        with patch.object(returns, "_rust_indent_prepared_api_return_description", spy):
            rendered = docs.render_table({"field": {"description": desc, "type": "string"}}, 2)
            key_row = docs.render_desc("key: first\nsecond", 1, "string")
        self.assertEqual(
            rendered,
            [
                (
                    '  * `field`: <span class="api-field-type">string</span>\n\n'
                    "      First\r\n      Second\u2028same\n        Third"
                )
            ],
        )
        self.assertEqual(
            key_row, ' * key: <span class="api-field-type">string</span>\n\n     first\n     second'
        )
        if spy is not None:
            self.assertEqual(
                [call.args for call in spy.call_args_list],
                [(desc, "\n      "), ("key: first\nsecond", "\n     ")],
            )

    def test_subclass_replace_keeps_python_dispatch(self) -> None:
        calls: list[tuple[str, str, SupportsIndex]] = []

        class Derived(str):
            __slots__ = ()

            @override
            def replace(self, old: str, new: str, count: SupportsIndex = -1, /) -> str:
                calls.append((old, new, count))
                return "different original behavior"

        docs = returns.APIReturnValuesTablePreprocessor(Markdown(), {})
        with patch.object(returns, "_rust_indent_prepared_api_return_description") as native:
            self.assertEqual(
                docs.render_desc(Derived("mock value\nmore"), 2, "string"),
                "  * different original behavior",
            )
        native.assert_not_called()
        self.assertEqual(calls, [("\n", "\n      ", -1)])

    def test_decline_and_negative_spacing_keep_original_values(self) -> None:
        docs = returns.APIReturnValuesTablePreprocessor(Markdown(), {})
        with patch.object(
            returns, "_rust_indent_prepared_api_return_description", return_value=None
        ) as native:
            self.assertEqual(docs.render_desc("\ud800\r\nnext", -8, "string"), "* \ud800\r\nnext")
        native.assert_called_once_with("\ud800\r\nnext", "\n")
