import os
from importlib import import_module
from typing import cast

from markdown import Markdown
from markdown.postprocessors import AndSubstitutePostprocessor as PythonAndSubstitutePostprocessor
from markdown.postprocessors import RawHtmlPostprocessor as PythonRawHtmlPostprocessor
from markdown.util import AtomicString, HtmlStash
from typing_extensions import override

_rust_postprocessing = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_POSTPROCESSING") == "1"
    else None
)

_python_is_block_level = Markdown.is_block_level
_python_isblocklevel = PythonRawHtmlPostprocessor.isblocklevel
_python_stash_to_string = PythonRawHtmlPostprocessor.stash_to_string
_python_block_level_regex = PythonRawHtmlPostprocessor.BLOCK_LEVEL_REGEX


class RawHtmlPostprocessor(PythonRawHtmlPostprocessor):
    @override
    def run(self, text: str) -> str:
        stash = self.md.htmlStash
        if (
            _rust_postprocessing is not None
            and type(text) in (str, AtomicString)
            and type(stash) is HtmlStash
            and type(stash.html_counter) is int
            and type(stash.rawHtmlBlocks) is list
            and stash.html_counter == len(stash.rawHtmlBlocks)
            and all(type(html) in (str, AtomicString) for html in stash.rawHtmlBlocks)
            and type(self.md.block_level_elements) is list
            and all(type(tag) is str for tag in self.md.block_level_elements)
            and getattr(self.md.is_block_level, "__func__", None) is _python_is_block_level
            and getattr(self.isblocklevel, "__func__", None) is _python_isblocklevel
            and getattr(self.stash_to_string, "__func__", None) is _python_stash_to_string
            and self.BLOCK_LEVEL_REGEX is _python_block_level_regex
        ):
            if stash.html_counter == 0:
                return text
            blocks = cast(list[str], stash.rawHtmlBlocks)
            result = _rust_postprocessing.restore_markdown_html_stash(
                text, [(html, self.isblocklevel(html)) for html in blocks]
            )
            if result is not None:
                return result
        return super().run(text)


class AndSubstitutePostprocessor(PythonAndSubstitutePostprocessor):
    @override
    def run(self, text: str) -> str:
        if _rust_postprocessing is not None and type(text) in (str, AtomicString):
            result = _rust_postprocessing.restore_markdown_ampersands(text)
            if result is not None:
                return result
        return super().run(text)
