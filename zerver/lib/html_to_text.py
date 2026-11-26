from collections.abc import Iterator, Mapping
from typing import cast

from bs4 import BeautifulSoup, Tag
from django.utils.html import escape

from zerver.lib.cache import cache_with_key, open_graph_description_cache_key
from zerver.lib.native_backend import load_native_contract

try:
    _rust_description_text = load_native_contract(
        "ZULIP_RUST_DESCRIPTION_TEXT", "DESCRIPTION_TEXT_API"
    )
except ImportError:
    _rust_description_text = None

_rust_normalize_description_text = getattr(
    _rust_description_text, "normalize_description_text", None
)


def _normalize_description_text(text: str) -> str:
    if _rust_normalize_description_text is not None:
        native = _rust_normalize_description_text(text)
        if native is not None:
            return native
    return " ".join(text.split())


_description_segments_backend = load_native_contract(
    "ZULIP_RUST_DESCRIPTION_SEGMENTS", "DESCRIPTION_SEGMENTS_API"
)
_reference_soup = BeautifulSoup
_reference_find_all = BeautifulSoup.find_all
_reference_tag_text = Tag.text


def _prepared_description_segments(elements: list[Tag]) -> Iterator[tuple[str, str]]:
    for element in elements:
        if element.text:
            yield element.name, element.text


def html_to_text(content: str | bytes, tags: Mapping[str, str] = {"p": " | "}) -> str:
    bs = BeautifulSoup(content, features="lxml")
    # Skip any admonition (warning) blocks, since they're
    # usually something about users needing to be an
    # organization administrator, and not useful for
    # describing the page.
    for tag in bs.find_all("div", class_="admonition"):
        tag.clear()

    # Skip tabbed-sections, which just contain navigation instructions.
    for tag in bs.find_all("div", class_="tabbed-section"):
        tag.clear()

    elements = bs.find_all(tags.keys())
    native = getattr(_description_segments_backend, "collect_prepared_description_segments", None)
    if (
        callable(native)
        and type(content) in (str, bytes)
        and type(tags) is dict
        and all(type(key) is str and type(value) is str for key, value in tags.items())
        and BeautifulSoup is _reference_soup
        and BeautifulSoup.find_all is _reference_find_all
        and Tag.text is _reference_tag_text
    ):
        text = cast("str", native(_prepared_description_segments(elements), tags))
        return escape(_normalize_description_text(text))

    text = ""
    for element in elements:
        # Ignore empty elements
        if not element.text:
            continue
        # .text converts it from HTML to text
        if text:
            text += tags[element.name]
        text += element.text
        if len(text) > 500:
            break
    return escape(_normalize_description_text(text))


@cache_with_key(open_graph_description_cache_key, timeout=3600 * 24)
def get_content_description(content: bytes, request_url: str) -> str:
    return html_to_text(content)
