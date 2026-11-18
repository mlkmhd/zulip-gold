import os
from dataclasses import dataclass
from importlib import import_module
from re import Match, Pattern
from typing import cast

from markdown import inlinepatterns

_rust_emphasis = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_EMPHASIS") == "1"
    else None
)


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
    if _rust_emphasis is not None:
        processor.compiled_re = cast(Pattern[str], EmphasisPattern(processor.compiled_re, tag))
    return processor
