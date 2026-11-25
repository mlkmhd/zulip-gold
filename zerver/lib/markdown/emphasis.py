from dataclasses import dataclass
from re import Match, Pattern
from typing import cast

from markdown import inlinepatterns

from zerver.lib.native_backend import load_native_contract

_rust_emphasis = load_native_contract("ZULIP_RUST_MARKDOWN_EMPHASIS", "MARKDOWN_EMPHASIS_API")

STOCK_PATTERNS = {
    "del": r"(?<!~)(\~\~)([^~\n]+?)(\~\~)(?!~)",
    "em": r"(\*)(?!\s+)([^\*^\n]+)(?<!\s)\*",
    "strong": r"(\*\*)([^\n]+?)\2",
    "strong,em": r"(\*\*\*)(?!\s+)([^\*^\n]+)(?<!\s)\*\*\*",
}


@dataclass
class EmphasisMatch:
    data: str
    captures: tuple[str, ...]

    def start(self, group: int = 0) -> int:
        assert group == 0
        return 0

    def end(self, group: int = 0) -> int:
        assert group == 0
        return len(self.data)

    def group(self, group: int = 0) -> str:
        return self.data if group == 0 else self.captures[group - 1]

    def groups(self) -> tuple[str, ...]:
        return self.captures


class EmphasisPattern:
    def __init__(self, fallback: Pattern[str], kind: str) -> None:
        self.fallback = fallback
        self.kind = kind

    def match(self, data: str) -> Match[str] | None:
        assert _rust_emphasis is not None
        if type(data) is not str:
            return self.fallback.match(data)
        result = _rust_emphasis.find_markdown_emphasis(data, self.kind)
        if result is None:
            return self.fallback.match(data)
        (matched,) = result
        if matched is None:
            return None
        before, marker, text, closing, after = matched
        captures: tuple[str, ...] = (before, marker, text)
        if closing is not None:
            captures += (closing,)
        return cast(Match[str], EmphasisMatch(data, (*captures, after)))


def tag_pattern(pattern: str, tag: str) -> inlinepatterns.Pattern:
    processor = (
        inlinepatterns.DoubleTagPattern(pattern, tag)
        if tag == "strong,em"
        else inlinepatterns.SimpleTagPattern(pattern, tag)
    )
    if (
        _rust_emphasis is not None
        and type(pattern) is str
        and type(tag) is str
        and STOCK_PATTERNS.get(tag) == pattern
    ):
        processor.compiled_re = cast(Pattern[str], EmphasisPattern(processor.compiled_re, tag))
    return processor
