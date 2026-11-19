import os
import sys
from importlib import import_module
from typing import cast
from xml.etree.ElementTree import Element

from markdown import util
from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor

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


class InlineProcessor(PythonInlineProcessor):
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
