import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from importlib import import_module
from typing import cast

_rust_tex_syntax = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_TEX_SYNTAX") == "1"
    else None
)

_WRAPPED_TEX_RE = r"^(.*?)\B(?<!\$)\$\$(?P<body>[^\n_$](\\\$|[^$\n])*)\$\$(?!\$)\B(.*)$"


@dataclass
class TexMatch:
    data: str
    captures: tuple[str, str, str | None, str]

    def start(self, group: int = 0) -> int:
        assert group == 0
        return 0

    def end(self, group: int = 0) -> int:
        assert group == 0
        return len(self.data)

    def group(self, group: int | str = 0) -> str | None:
        if group == "body":
            return self.captures[1]
        if type(group) is not int or not 0 <= group <= len(self.captures):
            raise IndexError("no such group")
        return self.data if group == 0 else self.captures[group - 1]

    def groups(self) -> tuple[str, str, str | None, str]:
        return self.captures


class TexPattern:
    def __init__(self, fallback: re.Pattern[str]) -> None:
        self.fallback = fallback

    def match(self, data: str) -> re.Match[str] | None:
        finder = getattr(_rust_tex_syntax, "find_markdown_tex", None)
        if (
            finder is None
            or sys.implementation.name != "cpython"
            or sys.version_info[:2] != (3, 12)
            or self.fallback.pattern != _WRAPPED_TEX_RE
            or self.fallback.flags != re.DOTALL | re.UNICODE
        ):
            return self.fallback.match(data)
        result = finder(data, unicodedata.unidata_version)
        if result is None:
            return self.fallback.match(data)
        (matched,) = result
        if matched is None:
            return None
        return cast(re.Match[str], TexMatch(data, matched))


def tex_pattern(fallback: re.Pattern[str]) -> re.Pattern[str]:
    if _rust_tex_syntax is None:
        return fallback
    return cast(re.Pattern[str], TexPattern(fallback))
