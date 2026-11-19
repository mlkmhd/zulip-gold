import os
from collections.abc import Sequence
from importlib import import_module
from xml.etree.ElementTree import Element, SubElement

from markdown import Markdown
from markdown.extensions import tables
from typing_extensions import override

_rust_tables = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_MARKDOWN_TABLES") == "1" else None
)
_rust_table_layout = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_TABLE_LAYOUT") == "1"
    else None
)


class TableProcessor(tables.TableProcessor):
    config: dict[str, bool]

    @override
    def run(self, parent: Element, blocks: list[str]) -> None:
        if _rust_table_layout is None:
            super().run(parent, blocks)
            return
        result = _rust_table_layout.plan_markdown_table(
            blocks[0], bool(self.border), list(self.separator)
        )
        if result is None:
            super().run(parent, blocks)
            return
        align, header, rows = result
        blocks.pop(0)
        table = SubElement(parent, "table")
        self._build_planned_row(header, SubElement(table, "thead"), align)
        tbody = SubElement(table, "tbody")
        if not rows:
            tr = SubElement(tbody, "tr")
            for _ in align:
                SubElement(tr, "td")
        else:
            for cells in rows:
                self._build_planned_row(cells, tbody, align)

    def _build_planned_row(
        self, cells: Sequence[str], parent: Element, align: Sequence[str | None]
    ) -> None:
        tr = SubElement(parent, "tr")
        tag = "th" if parent.tag == "thead" else "td"
        for text, alignment in zip(cells, align, strict=True):
            cell = SubElement(tr, tag)
            cell.text = text
            if alignment:
                if self.config["use_align_attribute"]:
                    cell.set("align", alignment)
                else:
                    cell.set("style", f"text-align: {alignment};")

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
