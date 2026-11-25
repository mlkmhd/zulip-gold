from markdown.preprocessors import NormalizeWhitespace
from typing_extensions import override

from zerver.lib.native_backend import load_native_contract

_rust_markdown_whitespace = load_native_contract(
    "ZULIP_RUST_MARKDOWN_WHITESPACE", "MARKDOWN_WHITESPACE_API"
)
_normalize_markdown_whitespace = getattr(
    _rust_markdown_whitespace, "normalize_markdown_whitespace", None
)


class ZulipNormalizeWhitespace(NormalizeWhitespace):
    @override
    def run(self, lines: list[str]) -> list[str]:
        if callable(_normalize_markdown_whitespace) and type(lines) is list:
            width = self.md.tab_length
            if type(width) is int and -(2**31) <= width <= 4096:
                rust_result = _normalize_markdown_whitespace(lines, width)
                if rust_result is not None:
                    return rust_result
        return super().run(lines)
