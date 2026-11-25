from collections.abc import Sequence
from xml.etree.ElementTree import Element, SubElement

from markdown import Markdown
from markdown.extensions import tables
from typing_extensions import override

from zerver.lib.native_backend import load_native_contract

_rust_tables = load_native_contract("ZULIP_RUST_MARKDOWN_TABLES", "MARKDOWN_TABLES_API")
_rust_table_layout = load_native_contract(
    "ZULIP_RUST_MARKDOWN_TABLE_LAYOUT", "MARKDOWN_TABLE_LAYOUT_API"
)

_STOCK_TABLE_PATTERNS = {
    name: getattr(tables.TableProcessor, name) for name in ("RE_CODE_PIPES", "RE_END_BORDER")
}
_STOCK_TABLE_SPLIT, _STOCK_BUILD_ROW, _STOCK_EMPTY_ROW = (
    getattr(tables.TableProcessor, name) for name in ("_split", "_build_row", "_build_empty_row")
)


def _uses_callback(processor: object, name: str, original: object) -> bool:
    return getattr(getattr(processor, name, None), "__func__", None) is original


class TableProcessor(tables.TableProcessor):
    config: dict[str, bool]

    def _has_stock_split(self) -> bool:
        return (
            type(self) is TableProcessor
            and all(
                getattr(self, name, None) is pattern
                for name, pattern in _STOCK_TABLE_PATTERNS.items()
            )
            and _uses_callback(self, "_split", _STOCK_TABLE_SPLIT)
        )

    @override
    def run(self, parent: Element, blocks: list[str]) -> None:
        if (
            _rust_table_layout is None
            or not self._has_stock_split()
            or not _uses_callback(self, "_split_row", _STOCK_ROW_SPLIT)
            or not _uses_callback(self, "_build_row", _STOCK_BUILD_ROW)
            or not _uses_callback(self, "_build_empty_row", _STOCK_EMPTY_ROW)
            or type(parent) is not Element
            or type(blocks) is not list
            or not blocks
            or type(blocks[0]) is not str
            or type(self.border) not in (bool, int)
            or type(self.separator) not in (list, tuple, str)
            or any(type(cell) is not str for cell in self.separator)
        ):
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
        if (
            _rust_tables is not None
            and type(block) is str
            and self._has_stock_split()
            and _uses_callback(self, "_split_row", _STOCK_ROW_SPLIT)
        ):
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
        if (
            _rust_tables is not None
            and type(row) is str
            and type(self.border) in (bool, int)
            and self._has_stock_split()
        ):
            result = _rust_tables.split_markdown_table_row(row, bool(self.border))
            if result is not None:
                return result
        return super()._split_row(row)  # type: ignore[misc] # private method missing from stubs


_STOCK_ROW_SPLIT = TableProcessor._split_row


class TableExtension(tables.TableExtension):
    @override
    def extendMarkdown(self, md: Markdown) -> None:
        if "|" not in md.ESCAPED_CHARS:
            md.ESCAPED_CHARS.append("|")
        processor = TableProcessor(md.parser, self.getConfigs())
        md.parser.blockprocessors.register(processor, "table", 75)
