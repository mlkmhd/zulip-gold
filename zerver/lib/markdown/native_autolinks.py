import re
import sys
import unicodedata
from functools import lru_cache
from typing import TYPE_CHECKING, cast

from django.conf import settings
from markdown import util

if TYPE_CHECKING:
    from zerver.lib.markdown import AutoLink

AutolinkSpec = tuple[str, list[str], list[str], bool]


@lru_cache(8)
def expression(tlds: tuple[str, ...], schemes: tuple[str, ...], files: bool) -> str:
    from zerver.lib.markdown import web_link_regex

    return "^(.*?)" + web_link_regex("|".join(tlds), "|".join(schemes), files) + "(.*?)$"


def snapshot(pattern: "AutoLink") -> AutolinkSpec | None:
    from zerver.lib import markdown

    tld_data: object = vars(markdown).get("tld_set")
    scheme_data: object = vars(markdown).get("auto_linked_schemes")
    if (
        sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 12)
        or unicodedata.unidata_version != "15.0.0"
        or (
            markdown.url_to_a,
            markdown.web_link_regex,
            markdown.list_of_tlds,
            vars(markdown).get("Element"),
            util.AtomicString,
        )
        != markdown._native_autolink_callbacks
        or type(tld_data) is not set
        or type(scheme_data) is not list
        or any(type(value) is not str or not value.isascii() for value in (*tld_data, *scheme_data))
        or type(cast(object, settings.ENABLE_FILE_LINKS)) is not bool
        or type(cast(object, pattern.compiled_re)) is not re.Pattern
        or pattern.compiled_re.flags != re.DOTALL | re.VERBOSE | re.UNICODE
    ):
        return None
    tlds = markdown.list_of_tlds()
    schemes = markdown.auto_linked_schemes[:]
    files = settings.ENABLE_FILE_LINKS
    if pattern.compiled_re.pattern != expression(tuple(tlds), tuple(schemes), files):
        return None
    return unicodedata.unidata_version, tlds, schemes, files
