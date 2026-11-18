import os
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import import_module
from re import Match, Pattern
from typing import cast

_rust_inline_code = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_INLINE_CODE") == "1"
    else None
)


@dataclass
class BacktickMatch:
    match_start: int
    match_end: int
    captures: tuple[str | None, ...]
    rendered_text: str

    def start(self, group: int = 0) -> int:
        assert group == 0
        return self.match_start

    def end(self, group: int = 0) -> int:
        assert group == 0
        return self.match_end

    def group(self, group: int = 0) -> str | None:
        return self.captures[group]


class BacktickPattern:
    def __init__(self, fallback: Pattern[str]) -> None:
        self.fallback = fallback

    def finditer(self, data: str, pos: int = 0) -> Iterator[Match[str]]:
        assert _rust_inline_code is not None
        while True:
            result = _rust_inline_code.find_markdown_inline_code(data, pos)
            if result is None:
                yield from self.fallback.finditer(data, pos)
                return
            (matched,) = result
            if matched is None:
                return
            start, end, backslashes, delimiter, code, rendered_text = matched
            # Python-Markdown uses only group() and the whole-match offsets.
            yield cast(
                Match[str],
                BacktickMatch(
                    start, end, (data[start:end], backslashes, delimiter, code), rendered_text
                ),
            )
            pos = end


def backtick_pattern(fallback: Pattern[str]) -> Pattern[str]:
    if _rust_inline_code is None:
        return fallback
    return cast(Pattern[str], BacktickPattern(fallback))
