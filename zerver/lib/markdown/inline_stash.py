import os
import sys
from importlib import import_module
from typing import cast
from xml.etree.ElementTree import Element

from markdown import util
from markdown.inlinepatterns import InlineProcessor as InlinePattern
from markdown.inlinepatterns import Pattern
from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor
from typing_extensions import override

from zerver.lib.markdown import native_inline

_rust_inline_stash = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_INLINE_STASH") == "1"
    else None
)

_python_find_placeholder = PythonInlineProcessor._InlineProcessor__findPlaceholder  # type: ignore[attr-defined] # Incomplete upstream stubs.
_python_process_element_text = PythonInlineProcessor._InlineProcessor__processElementText  # type: ignore[attr-defined] # Incomplete upstream stubs.
_python_placeholder_pattern = util.INLINE_PLACEHOLDER_RE
_rust_inline_patterns = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_INLINE_PATTERNS") == "1"
    else None
)
_rust_inline_apply = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_INLINE_APPLY") == "1"
    else None
)
_rust_inline_tree = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_INLINE_TREE") == "1"
    else None
)

_rust_inline_engine = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_INLINE_ENGINE") == "1"
    else None
)


_rust_native_inline = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_NATIVE_INLINE") == "1"
    else None
)


class InlineProcessor(PythonInlineProcessor):
    @override
    def run(self, tree: Element, ancestors: list[str] | None = None) -> Element:
        if _rust_native_inline is not None and native_inline.try_run(
            self,
            tree,
            ancestors,
            _rust_native_inline.plan_markdown_native_inline,
            _rust_native_inline.plan_markdown_native_inline_media,
            getattr(_rust_native_inline, "plan_markdown_native_inline_html", None),
            getattr(_rust_native_inline, "plan_markdown_native_inline_autolinks", None),
        ):
            return tree
        if _rust_inline_engine is not None:
            return cast(
                Element,
                _rust_inline_engine.run_markdown_inline_engine(
                    self, tree, ancestors, _inline_engine_callbacks
                ),
            )
        if _rust_inline_tree is not None:
            return cast(Element, _rust_inline_tree.run_markdown_inline(self, tree, ancestors))
        return super().run(tree, ancestors)

    def _InlineProcessor__applyPattern(
        self,
        pattern: Pattern,
        data: str,
        patternIndex: int,  # noqa: N803
        startIndex: int = 0,  # noqa: N803
    ) -> tuple[str, bool, int]:
        if _rust_inline_apply is not None and isinstance(pattern, InlinePattern):
            return cast(
                tuple[str, bool, int],
                _rust_inline_apply.apply_markdown_inline(
                    self, pattern, data, patternIndex, startIndex
                ),
            )
        return super()._InlineProcessor__applyPattern(pattern, data, patternIndex, startIndex)  # type: ignore[misc] # Incomplete upstream stubs.

    def _InlineProcessor__handleInline(
        self,
        data: str,
        patternIndex: int = 0,  # noqa: N803
    ) -> str:
        if isinstance(data, util.AtomicString):
            return data
        if (
            _rust_inline_patterns is not None
            and type(patternIndex) is int
            and 0 <= patternIndex <= sys.maxsize
        ):
            return cast(str, _rust_inline_patterns.handle_markdown_inline(self, data, patternIndex))
        return super()._InlineProcessor__handleInline(data, patternIndex)  # type: ignore[misc] # Incomplete upstream stubs.

    def _InlineProcessor__processPlaceholders(
        self,
        data: str | None,
        parent: Element,
        isText: bool = True,  # noqa: N803
    ) -> list[tuple[Element, list[str]]]:
        scanner = None
        if (
            _rust_inline_stash is not None
            and type(data) in (str, util.AtomicString)
            and self._InlineProcessor__placeholder_prefix == "\x02klzzwxh:"  # type: ignore[attr-defined] # Incomplete upstream stubs.
            and self._InlineProcessor__placeholder_re is _python_placeholder_pattern  # type: ignore[attr-defined] # Incomplete upstream stubs.
            and getattr(self._InlineProcessor__findPlaceholder, "__func__", None)  # type: ignore[attr-defined] # Incomplete upstream stubs.
            is _python_find_placeholder
            and getattr(self._InlineProcessor__processElementText, "__func__", None)  # type: ignore[attr-defined] # Incomplete upstream stubs.
            is _python_process_element_text
        ):
            scanner = _rust_inline_stash.scan_markdown_inline_stash(data)
        if scanner is None:
            return super()._InlineProcessor__processPlaceholders(data, parent, isText)  # type: ignore[misc] # Incomplete upstream stubs.

        result: list[tuple[Element, list[str]]] = []

        def link_text(text: str) -> None:
            if not text:
                return
            if result:
                node = result[-1][0]
                if node.tail:
                    node.tail += text
                else:
                    node.tail = text
            elif not isText:
                if parent.tail:
                    parent.tail += text
                else:
                    parent.tail = text
            else:
                if parent.text:
                    parent.text += text
                else:
                    parent.text = text

        accepted = False
        while (step := scanner.next(accepted)) is not None:
            text, rejected_text, identifier = step
            if rejected_text is None:
                link_text(util.AtomicString(text) if isinstance(data, util.AtomicString) else text)
                break
            accepted = identifier in self.stashed_nodes
            if not accepted:
                link_text(rejected_text)
                continue
            node = cast(Element | str, self.stashed_nodes.get(identifier))
            link_text(text)
            if isinstance(node, str):
                link_text(node)
                continue
            for child in [node, *list(node)]:
                if child.tail and child.tail.strip():
                    self._InlineProcessor__processElementText(node, child, False)  # type: ignore[attr-defined] # Incomplete upstream stubs.
                if child.text and child.text.strip():
                    self._InlineProcessor__processElementText(child, child)  # type: ignore[attr-defined] # Incomplete upstream stubs.
            result.append((node, self.ancestors[:]))
        return result


_inline_engine_callbacks = {
    name: getattr(InlineProcessor, "_InlineProcessor" + name)
    for name in [
        "__handleInline",
        "__applyPattern",
        "__processPlaceholders",
        "__processElementText",
        "__build_ancestors",
        "__stashNode",
        "__makePlaceholder",
    ]
}
