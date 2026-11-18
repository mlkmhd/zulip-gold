import os
from importlib import import_module
from xml.etree.ElementTree import Element

from markdown import Markdown
from markdown.extensions import tables
from typing_extensions import override

_rust_tables = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_MARKDOWN_TABLES") == "1" else None
)


class TableProcessor(tables.TableProcessor):
    @override
    def test(self, parent: Element, block: str) -> bool:
        if _rust_tables is not None:
            result = _rust_tables.check_markdown_table(block)
            if result is not None:
                is_table, border, separator = result
                if border is not None:
                    self.border = border
                if separator is not None:
                    self.separator = separator
                return is_table
        return super().test(parent, block)

    @override
    def _split_row(self, row: str) -> list[str]:  # type: ignore[misc] # private method missing from stubs
        if _rust_tables is not None:
            result = _rust_tables.split_markdown_table_row(row, bool(self.border))
            if result is not None:
                return result
        return super()._split_row(row)  # type: ignore[misc] # private method missing from stubs


class TableExtension(tables.TableExtension):
    @override
    def extendMarkdown(self, md: Markdown) -> None:
        if "|" not in md.ESCAPED_CHARS:
            md.ESCAPED_CHARS.append("|")
        processor = TableProcessor(md.parser, self.getConfigs())
        md.parser.blockprocessors.register(processor, "table", 75)
