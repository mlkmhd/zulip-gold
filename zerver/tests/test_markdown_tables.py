import random
from contextlib import nullcontext
from unittest import TestCase, mock
from xml.etree.ElementTree import Element

from markdown import Markdown
from markdown.extensions import tables

from zerver.lib.markdown.tables import TableExtension, TableProcessor


class MarkdownTableTest(TestCase):
    def render(self, content: str, use_python: bool = False, align_attribute: bool = False) -> str:
        engine = Markdown(extensions=[TableExtension(use_align_attribute=align_attribute)])
        context = (
            mock.patch("zerver.lib.markdown.tables._rust_tables", None)
            if use_python
            else nullcontext()
        )
        with context:
            return engine.convert(content)

    def test_rendering_and_alignment(self) -> None:
        for content in [
            "| عنوان | کد |\n| :--- | ---: |\n| **پررنگ** | `a|b` |",
            "a|b|c\n:-|:-:|-:\n1|2|3\n4|5\n6|7|8|9",
            "| h |\n|---|",
            "| h |\n|---|\n| value |",
            "h|h\n--|x\na|b",
            "a|b\n--|--\na\\|b|c",
            "| a | b |\n|---|---|\n| \\``a|b` | c |",
            "| a | b |\n|---|---|\n| <script>& | [link](https://zulip.com) |",
            "| \ud800 | b |\n|---|---|\n| c | d |",
        ]:
            for align_attribute in [False, True]:
                with self.subTest(content=content, align_attribute=align_attribute):
                    self.assertEqual(
                        self.render(content, align_attribute=align_attribute),
                        self.render(content, use_python=True, align_attribute=align_attribute),
                    )
        self.assertIn('<th style="text-align: center;">', self.render("a|b\n:-:|--"))
        self.assertIn('<th align="center">', self.render("a|b\n:-:|--", align_attribute=True))

    def test_row_splitting(self) -> None:
        rng = random.Random(0)
        engine = Markdown()
        for _ in range(3000):
            row = "".join(rng.choices("|`\\a 📝عنوان\n\r", k=rng.randrange(70)))
            for border in [0, 1, 2, 3]:
                processor = TableProcessor(engine.parser, {"use_align_attribute": False})
                processor.border = border  # type: ignore[assignment] # stubs omit integer border flags
                with self.subTest(row=row, border=border):
                    self.assertEqual(
                        processor._split_row(row),
                        tables.TableProcessor._split_row(processor, row),  # type: ignore[attr-defined] # private method missing from stubs
                    )
        processor = TableProcessor(engine.parser, {"use_align_attribute": False})
        self.assertEqual(processor._split_row("\ud800|b"), ["\ud800", "b"])

    def test_table_detection_retains_previous_state(self) -> None:
        engine = Markdown()
        processor = TableProcessor(engine.parser, {"use_align_attribute": False})
        reference = tables.TableProcessor(engine.parser, {"use_align_attribute": False})
        rng = random.Random(1)
        blocks = [
            "| h |\n| :---: |",
            "single",
            "x|y\n--|bad",
            "| h |\n|",
            "| a |\n--\nplain",
            "\ud800|b\n--|--",
        ]
        blocks.extend(
            "\n".join(
                "".join(rng.choices("|`\\a :- ", k=rng.randrange(30)))
                for _ in range(rng.randrange(1, 5))
            )
            for _ in range(2000)
        )
        for block in blocks:
            with self.subTest(block=block):
                self.assertEqual(
                    processor.test(Element("div"), block), reference.test(Element("div"), block)
                )
                self.assertEqual(processor.border, reference.border)
                self.assertEqual(processor.separator, reference.separator)

    def test_rust_parser_is_used(self) -> None:
        from zerver.lib.markdown import tables as tables_module

        if tables_module._rust_tables is None:
            self.skipTest("Rust table parser is disabled")
        with (
            mock.patch.object(tables.TableProcessor, "test", side_effect=AssertionError),
            mock.patch.object(tables.TableProcessor, "_split_row", side_effect=AssertionError),
        ):
            rendered = self.render("| a | b |\n|---|---|\n| `a|b` | c |")
            self.assertIn("<td><code>a|b</code></td>", rendered)
