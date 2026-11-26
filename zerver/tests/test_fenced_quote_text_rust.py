from unittest.mock import patch

from django.test import SimpleTestCase
from markdown import Markdown

from zerver.lib.markdown import fenced_code


class FencedQuoteTextContractTest(SimpleTestCase):
    def test_render_quote_paragraphs_without_swallowing_following_text(self) -> None:
        engine = Markdown(extensions=[fenced_code.FencedCodeExtension()])
        self.assertEqual(
            engine.convert("~~~quote\nfirst\n\nsecond\n~~~\n\nafter"),
            "<blockquote>\n<p>first</p>\n<p>second</p>\n</blockquote>\n<p>after</p>",
        )

    def test_default_preserves_literal_line_boundaries(self) -> None:
        backend = fenced_code._rust_fenced_quote_text
        if getattr(backend, "FENCED_QUOTE_TEXT_API", None) != 1:
            self.skipTest("Matching fenced quote text contract is not available")
        source = "x" * (8 * 1024 * 1024) + "\ud800\n\nα\r\u2028\n"
        processor = fenced_code.FencedBlockPreprocessor(Markdown())
        with patch.object(
            backend,
            "prefix_exact_fenced_quote_lines",
            wraps=backend.prefix_exact_fenced_quote_lines,
        ) as prefix:
            self.assertEqual(processor.format_quote(source), "> " + source.replace("\n", "\n> "))
        prefix.assert_called_once_with(source)

    def test_custom_split_remains_in_python(self) -> None:
        class CustomSplit(str):
            __slots__ = ()

            def split(self, *args: object, **kwargs: object) -> list[str]:
                return ["custom"]

        processor = fenced_code.FencedBlockPreprocessor(Markdown())
        self.assertEqual(processor.format_quote(CustomSplit("original")), "> custom")
