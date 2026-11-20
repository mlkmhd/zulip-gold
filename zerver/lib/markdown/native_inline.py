import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast
from xml.etree.ElementTree import Element

import regex
from markdown import inlinepatterns, util

from zerver.lib.markdown.emphasis import EmphasisPattern
from zerver.lib.markdown.inline_code import BacktickPattern

if TYPE_CHECKING:
    from zerver.lib.markdown.inline_stash import InlineProcessor

Text: TypeAlias = tuple[str, bool]
Node: TypeAlias = tuple[str, str, Text | None, Text | None, list[int]]
PlannedNode: TypeAlias = tuple[str, str, Text | None, Text | None, list[int], list[tuple[str, str]]]
EmojiCatalog: TypeAlias = tuple[dict[str, str], dict[str, str]]
EmoticonCatalog: TypeAlias = tuple[list[tuple[str, str]], dict[str, str]]
Stashed: TypeAlias = tuple[int | None, Text | None]
Plan: TypeAlias = tuple[list[PlannedNode], list[tuple[int, int]], list[Stashed], list[str], bool]
Planner: TypeAlias = Callable[
    [
        list[Node],
        list[tuple[int, list[str]]],
        list[str],
        list[tuple[object, object | None]],
        list[EmojiCatalog | None],
        EmoticonCatalog | None,
    ],
    Plan | None,
]


class RegistryInternals(Protocol):
    _data: dict[str, object]
    _priority: list[tuple[str, float]]
    _is_sorted: object


_REGISTRY_CALLBACKS = {
    name: getattr(util.Registry, name) for name in ("__iter__", "__getitem__", "__len__", "_sort")
}
_PRIORITY_ITEM_TYPE = util._PriorityItem  # type: ignore[attr-defined] # Missing from upstream stubs.

_SURROGATE_RE = re.compile("[\ud800-\udfff]")
_EMPHASIS = {
    "strong,em": (2, r"(\*\*\*)(?!\s+)([^\*^\n]+)(?<!\s)\*\*\*"),
    "strong": (3, r"(\*\*)([^\n]+?)\2"),
    "em": (4, r"(\*)(?!\s+)([^\*^\n]+)(?<!\s)\*"),
    "del": (5, r"(?<!~)(\~\~)([^~\n]+?)(\~\~)(?!~)"),
}


def try_run(
    processor: "InlineProcessor", root: Element, ancestors: list[str] | None, planner: Planner
) -> bool:
    from zerver.lib import markdown
    from zerver.lib.emoji import EMOTICON_CONVERSIONS, EMOTICON_RE
    from zerver.lib.markdown import (
        TEXT_PRESENTATION_RE,
        BacktickInlineProcessor,
        DbData,
        Emoji,
        EmoticonTranslation,
        UnicodeEmoji,
        ZulipMarkdown,
        _native_emoji_callbacks,
        _native_inline_handlers,
        make_emoji,
        make_realm_emoji,
        name_to_codepoint,
    )
    from zerver.lib.markdown.inline_stash import (
        InlineProcessor,
        _inline_engine_callbacks,
        _python_find_placeholder,
        _python_placeholder_pattern,
    )

    if type(processor) is not InlineProcessor or any(
        getattr(getattr(processor, "_InlineProcessor" + name), "__func__", None) is not callback
        for name, callback in {
            **_inline_engine_callbacks,
            "__findPlaceholder": _python_find_placeholder,
        }.items()
    ):
        return False
    if (
        processor._InlineProcessor__placeholder_prefix != "\x02klzzwxh:"  # type: ignore[attr-defined] # Incomplete upstream stubs.
        or processor._InlineProcessor__placeholder_re is not _python_placeholder_pattern  # type: ignore[attr-defined] # Incomplete upstream stubs.
        or util.INLINE_PLACEHOLDER != "\x02klzzwxh:%s\x03"
        or type(processor.inlinePatterns) is not util.Registry
        or (ancestors is not None and type(ancestors) is not list)
        or any(type(tag) is not str or _SURROGATE_RE.search(tag) for tag in ancestors or [])
    ):
        return False

    registry = processor.inlinePatterns
    state = cast(RegistryInternals, registry)
    if any(
        getattr(getattr(registry, name), "__func__", None) is not callback
        for name, callback in _REGISTRY_CALLBACKS.items()
    ):
        return False
    data: object = state._data
    priorities: object = state._priority
    if (
        type(data) is not dict
        or type(priorities) is not list
        or type(state._is_sorted) is not bool
        or type(TEXT_PRESENTATION_RE) is not regex.Pattern
    ):
        return False
    if any(type(name) is not str for name in data) or any(
        type(item) is not _PRIORITY_ITEM_TYPE
        or type(item[0]) is not str
        or type(item[1]) not in (int, float)
        or item[0] not in data
        for item in priorities
    ):
        return False
    priorities = cast(list[tuple[str, float]], priorities)
    ordered = (
        priorities
        if state._is_sorted
        else sorted(priorities, key=lambda item: item[1], reverse=True)
    )
    patterns: list[tuple[int, list[str]]] = []
    guards: list[tuple[object, object | None]] = []
    emoji_catalogs: list[EmojiCatalog | None] = []
    emoticon_catalog = None
    for name, _ in ordered:
        pattern = cast(inlinepatterns.Pattern, data[name])
        callbacks = _native_inline_handlers.get(type(pattern))
        if callbacks is None or any(
            getattr(getattr(pattern, name), "__func__", None) is not callback
            for name, callback in zip(
                ("handleMatch", "getCompiledRegExp", "type"), callbacks, strict=True
            )
        ):
            return False
        excludes = pattern.ANCESTOR_EXCLUDES
        if type(excludes) not in (tuple, list) or any(
            type(tag) is not str or _SURROGATE_RE.search(tag) for tag in excludes
        ):
            return False
        wrapped: object = pattern.compiled_re
        compiled = wrapped
        if type(wrapped) in (BacktickPattern, EmphasisPattern):
            compiled = cast(BacktickPattern | EmphasisPattern, wrapped).fallback
        if type(compiled) not in (re.Pattern, regex.Pattern):
            return False
        expression = cast(re.Pattern[str] | regex.Pattern[str], compiled)
        kind = 0
        catalog = None
        if type(pattern) is BacktickInlineProcessor:
            if (
                expression.pattern != inlinepatterns.BACKTICK_RE
                or pattern.tag != "code"
                or pattern.ESCAPED_BSLASH != "\x0292\x03"
            ):
                return False
            kind = 1
        elif type(pattern) in (inlinepatterns.SimpleTagPattern, inlinepatterns.DoubleTagPattern):
            tag = cast(inlinepatterns.SimpleTagPattern, pattern).tag
            spec = _EMPHASIS.get(tag)
            if spec is None or expression.pattern != "^(.*?)" + spec[1] + "(.*)$":
                return False
            if (type(pattern) is inlinepatterns.DoubleTagPattern) != (tag == "strong,em"):
                return False
            kind = spec[0]
        elif type(pattern) is inlinepatterns.SimpleTextInlineProcessor:
            if expression.pattern != inlinepatterns.NOT_STRONG_RE:
                return False
            kind = 6
        elif type(pattern) is inlinepatterns.SubstituteTagInlineProcessor:
            if expression.pattern != r"\n" or pattern.tag != "br":
                return False
            kind = 7
        elif type(pattern) in (Emoji, EmoticonTranslation):
            renderer = cast(Emoji | EmoticonTranslation, pattern).zmd
            if (
                expression.pattern
                != "^(.*?)"
                + (r"(?P<syntax>:[\w\-\+]+:)" if type(pattern) is Emoji else EMOTICON_RE)
                + "(.*)$"
                or make_emoji is not _native_emoji_callbacks[0]
                or make_realm_emoji is not _native_emoji_callbacks[1]
                or type(renderer) is not ZulipMarkdown
                or renderer is not processor.md
                or type(name_to_codepoint) is not dict
                or any(
                    type(key) is not str
                    or type(value) is not str
                    or _SURROGATE_RE.search(key)
                    or _SURROGATE_RE.search(value)
                    for key, value in name_to_codepoint.items()
                )
            ):
                return False
            db_data = renderer.zulip_db_data
            if db_data is not None and type(db_data) is not DbData:
                return False
            if type(pattern) is EmoticonTranslation:
                if (
                    markdown.translate_emoticons is not _native_emoji_callbacks[2]
                    or type(EMOTICON_CONVERSIONS) is not dict
                    or any(
                        type(key) is not str
                        or not key
                        or _SURROGATE_RE.search(key)
                        or type(value) is not str
                        or _SURROGATE_RE.search(value)
                        or "\\" in value
                        for key, value in EMOTICON_CONVERSIONS.items()
                    )
                    or (
                        db_data is not None
                        and type(cast(object, db_data.translate_emoticons)) is not bool
                    )
                    or r"(?<![^,.;?!()\[\] \"'\n\t])(?P<emoticon>("
                    + r")|(".join(re.escape(key) for key in EMOTICON_CONVERSIONS)
                    + r"))(?![^,.;?!()\[\] \"'\n\t])"
                    != EMOTICON_RE
                ):
                    return False
                if db_data is not None and db_data.translate_emoticons:
                    emoticon_catalog = (list(EMOTICON_CONVERSIONS.items()), name_to_codepoint)
                kind = 9
            else:
                kind = 8
                active = {} if db_data is None else db_data.active_realm_emoji
                if type(active) is not dict or any(
                    type(name) is not str
                    or _SURROGATE_RE.search(name)
                    or type(info) is not dict
                    or type(info.get("source_url")) is not str
                    or _SURROGATE_RE.search(info["source_url"])
                    for name, info in active.items()
                ):
                    return False
                catalog = (
                    name_to_codepoint,
                    {name: info["source_url"] for name, info in active.items()},
                )
        if kind and (
            type(compiled) is not re.Pattern or expression.flags != re.DOTALL | re.UNICODE
        ):
            return False
        if type(wrapped) is BacktickPattern and type(pattern) is not BacktickInlineProcessor:
            return False
        if type(wrapped) is EmphasisPattern and (
            not isinstance(pattern, inlinepatterns.SimpleTagPattern) or wrapped.kind != pattern.tag
        ):
            return False
        patterns.append((kind, [tag.lower() for tag in excludes]))
        guards.append((compiled, TEXT_PRESENTATION_RE if type(pattern) is UnicodeEmoji else None))
        emoji_catalogs.append(catalog)

    if type(root) is not Element:
        return False
    originals = [root]
    indices = {root: 0}
    nodes: list[Node] = []
    size = 0
    for node in originals:
        if len(originals) > 20_000 or type(node) is not Element or type(node.tag) is not str:
            return False
        if any(
            text is not None
            and (type(text) not in (str, util.AtomicString) or _SURROGATE_RE.search(text))
            for text in (node.tag, node.text, node.tail)
        ):
            return False
        size += len(node.tag) + len(node.text or "") + len(node.tail or "")
        if size > 1_000_000:
            return False
        children = []
        for child in node:
            if type(child) is not Element:
                return False
            if child not in indices:
                indices[child] = len(originals)
                originals.append(child)
            children.append(indices[child])
        nodes.append(
            (
                node.tag,
                node.tag.lower(),
                None
                if node.text is None
                else (str(node.text), isinstance(node.text, util.AtomicString)),
                None
                if node.tail is None
                else (str(node.tail), isinstance(node.tail, util.AtomicString)),
                children,
            )
        )
    plan = planner(nodes, patterns, ancestors or [], guards, emoji_catalogs, emoticon_catalog)
    if plan is None:
        return False
    updates, parents, stash, final_ancestors, registry_used = plan
    elements = originals + [Element(node[0], dict(node[5])) for node in updates[len(originals) :]]

    def restore(text: Text | None) -> str | None:
        if text is None:
            return None
        value, atomic = text
        return util.AtomicString(value) if atomic else value

    for element, (_, _, text, tail, children, _) in zip(elements, updates, strict=True):
        element.text = restore(text)
        element.tail = restore(tail)
        element[:] = [elements[child] for child in children]
    if registry_used:
        state._priority[:] = ordered
        state._is_sorted = True
    processor.stashed_nodes = {
        f"{index:04}": elements[node] if node is not None else cast(str, restore(text))
        for index, (node, text) in enumerate(stash)
    }
    processor.parent_map = {elements[node]: elements[parent] for node, parent in parents}
    processor.ancestors = final_ancestors
    return True
