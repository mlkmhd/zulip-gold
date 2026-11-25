from collections.abc import Mapping

from bs4 import BeautifulSoup
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

    text = ""
    for element in bs.find_all(tags.keys()):
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
