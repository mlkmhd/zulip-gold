"""Opt-in native candidate discovery and resolution for topic links."""

import os
from importlib import import_module
from typing import TYPE_CHECKING, TypeAlias

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
    native = getattr(_backend, "select_markdown_topic_links", None)
    if native is None:
        return None
    return _format(native(_wire(matches)))
