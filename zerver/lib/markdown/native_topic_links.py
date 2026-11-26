"""Native resolution of prepared topic links, with optional discovery."""

import os
from importlib import import_module
from typing import TYPE_CHECKING, TypeAlias

from zerver.lib.native_backend import load_native_contract

if TYPE_CHECKING:
    from zerver.lib.markdown import TopicLinkMatch

WireCandidate: TypeAlias = tuple[str, str, int, int | None]

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_MARKDOWN_TOPIC_LINKS") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


_selection_backend = load_native_contract(
    "ZULIP_RUST_PREPARED_TOPIC_LINK_SELECTION", "PREPARED_TOPIC_LINK_SELECTION_API"
)


def _wire(matches: list["TopicLinkMatch"]) -> list[WireCandidate]:
    return [(match.url, match.text, match.index, match.precedence) for match in matches]


def _format(result: list[tuple[str, str]] | None) -> list[dict[str, str]] | None:
    if result is None:
        return None
    return [{"url": url, "text": text} for url, text in result]


def plan(topic: str, linkifiers: list["TopicLinkMatch"]) -> list[dict[str, str]] | None:
    native = getattr(_backend, "plan_markdown_topic_links", None)
    if native is None or type(topic) is not str or not topic:
        return None

    from zerver.lib import markdown
    from zerver.lib.markdown.native_autolinks import spec_for_regex

    current = (
        markdown.re.match,
        markdown.get_web_link_regex,
        markdown.web_link_regex,
        markdown.list_of_tlds,
        markdown.urlsplit,
        markdown.basic_link_splitter,
    )
    if any(
        found is not reference
        for found, reference in zip(current, markdown._topic_link_scan_callbacks, strict=True)
    ):
        return None
    spec = spec_for_regex(markdown.get_web_link_regex())
    if spec is None:
        return None
    return _format(native(topic, _wire(linkifiers), spec))


def select(matches: list["TopicLinkMatch"]) -> list[dict[str, str]] | None:
    legacy = getattr(_backend, "select_markdown_topic_links", None)
    if legacy is not None:
        return _format(legacy(_wire(matches)))
    native = getattr(_selection_backend, "select_prepared_topic_link_indices", None)
    if native is None or type(matches) is not list:
        return None

    from zerver.lib.markdown import TopicLinkMatch

    wire: list[WireCandidate] = []
    for match in matches:
        if (
            type(match) is not TopicLinkMatch
            or type(match.url) is not str
            or type(match.text) is not str
            or type(match.index) is not int
            or (match.precedence is not None and type(match.precedence) is not int)
        ):
            return None
        wire.append((match.url, match.text, match.index, match.precedence))
    selected = native(wire)
    if selected is None:
        return None
    # URL discovery and the payload objects remain exactly as Python prepared them.
    return [{"url": matches[i].url, "text": matches[i].text} for i in selected]
