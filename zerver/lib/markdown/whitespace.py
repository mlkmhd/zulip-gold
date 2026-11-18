import os
from importlib import import_module

from markdown.preprocessors import NormalizeWhitespace
from typing_extensions import override

_rust_markdown_whitespace = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_WHITESPACE") == "1"
    else None
)


class ZulipNormalizeWhitespace(NormalizeWhitespace):
    @override
    def run(self, lines: list[str]) -> list[str]:
        if _rust_markdown_whitespace is not None:
            rust_result = _rust_markdown_whitespace.normalize_markdown_whitespace(
                lines, self.md.tab_length
            )
            if rust_result is not None:
                return rust_result
        return super().run(lines)
