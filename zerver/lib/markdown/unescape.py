from markdown.treeprocessors import UnescapeTreeprocessor as PythonUnescapeTreeprocessor
from markdown.util import AtomicString
from typing_extensions import override

from zerver.lib.native_backend import load_native_contract

_rust_unescape = load_native_contract("ZULIP_RUST_MARKDOWN_UNESCAPE", "MARKDOWN_UNESCAPE_API")


_python_unescape = PythonUnescapeTreeprocessor._unescape  # type: ignore[attr-defined] # Incomplete upstream stubs.
_python_pattern = PythonUnescapeTreeprocessor.RE


class UnescapeTreeprocessor(PythonUnescapeTreeprocessor):
    @override
    def unescape(self, text: str) -> str:
        if (
            _rust_unescape is not None
            and type(text) in (str, AtomicString)
            and self.RE is _python_pattern
            and getattr(self._unescape, "__func__", None) is _python_unescape  # type: ignore[attr-defined] # Incomplete upstream stubs.
        ):
            result = _rust_unescape.unescape_markdown_text(text)
            if result is not None:
                return result
        return super().unescape(text)
