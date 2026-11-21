"""
Fenced Code Extension for Python Markdown
=========================================

This extension adds Fenced Code Blocks to Python-Markdown.

    >>> import markdown
    >>> text = '''
    ... A paragraph before a fenced code block:
    ...
    ... ~~~
    ... Fenced code block
    ... ~~~
    ... '''
    >>> html = markdown.markdown(text, extensions=['fenced_code'])
    >>> print html
    <p>A paragraph before a fenced code block:</p>
    <pre><code>Fenced code block
    </code></pre>

Works with safe_mode also (we check this because we are using the HtmlStash):

    >>> print markdown.markdown(text, extensions=['fenced_code'], safe_mode='replace')
    <p>A paragraph before a fenced code block:</p>
    <pre><code>Fenced code block
    </code></pre>

Include tilde's in a code block and wrap with blank lines:

    >>> text = '''
    ... ~~~~~~~~
    ...
    ... ~~~~
    ... ~~~~~~~~'''
    >>> print markdown.markdown(text, extensions=['fenced_code'])
    <pre><code>
    ~~~~
    </code></pre>

Removes trailing whitespace from code blocks that cause horizontal scrolling
    >>> import markdown
    >>> text = '''
    ... A paragraph before a fenced code block:
    ...
    ... ~~~
    ... Fenced code block    \t\t\t\t\t\t\t
    ... ~~~
    ... '''
    >>> html = markdown.markdown(text, extensions=['fenced_code'])
    >>> print html
    <p>A paragraph before a fenced code block:</p>
    <pre><code>Fenced code block
    </code></pre>

Language tags:

    >>> text = '''
    ... ~~~~{.python}
    ... # Some python code
    ... ~~~~'''
    >>> print markdown.markdown(text, extensions=['fenced_code'])
    <pre><code class="python"># Some python code
    </code></pre>

Copyright 2007-2008 [Waylan Limberg](http://achinghead.com/).

Project website: <http://packages.python.org/Markdown/extensions/fenced_code_blocks.html>
Contact: markdown@freewisdom.org

License: BSD (see ../docs/LICENSE for details)

Dependencies:
* [Python 2.4+](http://python.org)
* [Markdown 2.0+](http://packages.python.org/Markdown/)
* [Pygments (optional)](http://pygments.org)

"""

import logging
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Iterable, Mapping, MutableSequence, Sequence
from importlib import import_module
from typing import Any, cast

import lxml.html
from django.utils.html import escape
from markdown import Markdown
from markdown.extensions import Extension, codehilite
from markdown.extensions.codehilite import CodeHiliteExtension, parse_hl_lines
from markdown.preprocessors import Preprocessor
from pygments.lexers import find_lexer_class_by_name
from pygments.util import ClassNotFound
from typing_extensions import override

from zerver.lib.exceptions import MarkdownRenderingError
from zerver.lib.markdown.priorities import PREPROCESSOR_PRIORITIES
from zerver.lib.markdown.whitespace import ZulipNormalizeWhitespace
from zerver.lib.tex import render_tex
from zerver.models.realm_playgrounds import PLAYGROUND_LANGUAGE_REGEX

_native_parse_hl_lines = parse_hl_lines

_rust_code_header = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_CODE_HEADER") == "1" else None
)

_rust_code_highlight = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_CODE_HIGHLIGHT") == "1" else None
)

_rust_tex_handlers = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_MARKDOWN_TEX_HANDLERS") == "1"
    else None
)

_rust_markdown_fences = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_MARKDOWN_FENCES") == "1" else None
)
_rust_markdown_blocks = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_MARKDOWN_BLOCKS") == "1" else None
)

# Global vars
FENCE_RE = re.compile(
    r"""
    # ~~~ or ```
    (?P<fence>
        ^(?:~{3,}|`{3,})
    )

    [ ]* # spaces

    (?:
        # language, like ".py" or "{javascript}"
        \{?\.?
        (?P<lang>
            """
    + PLAYGROUND_LANGUAGE_REGEX
    + r"""
        ) # "py" or "javascript"

        [ ]* # spaces

        # header for features that use fenced block header syntax (like spoilers)
        (?P<header>
            [^ ~`][^~`]*
        )?
        \}?
    )?
    $
    """,
    re.VERBOSE,
)


CODE_WRAP = "<pre><code{}>{}\n</code></pre>"
LANG_TAG = ' class="{}"'


def validate_curl_content(lines: list[str]) -> None:
    error_msg = """
Missing required -X argument in curl command:

{command}
""".strip()

    for line in lines:
        regex = r'curl [-](sS)?X "?(GET|DELETE|PATCH|POST)"?'
        if line.startswith("curl") and re.search(regex, line) is None:
            raise MarkdownRenderingError(error_msg.format(command=line.strip()))


CODE_VALIDATORS: dict[str | None, Callable[[list[str]], None]] = {
    "curl": validate_curl_content,
}


# This function is similar to one used in fenced_code.ts
def get_unused_fence(content: str) -> str:
    choose_fence = getattr(_rust_markdown_fences, "get_unused_markdown_fence", None)
    if choose_fence is not None and type(content) is str:
        result = choose_fence(content)
        if result is not None:
            return cast(str, result)

    # Define the regular expression pattern to match ``` fences
    fence_length_re = re.compile(r"^ {0,3}(`{3,})", re.MULTILINE)

    # Initialize the length variable to 3, corresponding to default fence length
    length = 3
    matches = fence_length_re.findall(content)
    for match in matches:
        length = max(length, len(match) + 1)

    return "`" * length


class FencedCodeExtension(Extension):
    def __init__(self, config: Mapping[str, Any] = {}) -> None:
        self.config = {
            "run_content_validators": [
                config.get("run_content_validators", False),
                "Boolean specifying whether to run content validation code in CodeHandler",
            ],
        }

        for key, value in config.items():
            self.setConfig(key, value)

    @override
    def extendMarkdown(self, md: Markdown) -> None:
        """Add FencedBlockPreprocessor to the Markdown instance."""
        md.registerExtension(self)
        processor = FencedBlockPreprocessor(
            md, run_content_validators=self.config["run_content_validators"][0]
        )
        md.preprocessors.register(
            processor, "fenced_code_block", PREPROCESSOR_PRIORITIES["fenced_code_block"]
        )


class ZulipBaseHandler:
    def __init__(
        self,
        processor: "FencedBlockPreprocessor",
        output: MutableSequence[str],
        fence: str | None = None,
        process_contents: bool = False,
    ) -> None:
        self.processor = processor
        self.output = output
        self.fence = fence
        self.process_contents = process_contents
        self.lines: list[str] = []

    def handle_line(self, line: str) -> None:
        if line.rstrip() == self.fence:
            self.done()
        else:
            self.lines.append(line.rstrip())

    def done(self) -> None:
        finish_native = getattr(_rust_markdown_fences, "finish_fenced_block", None)
        if finish_native is not None:
            finish_native(self)
            return
        if self.lines:
            text = "\n".join(self.lines)
            text = self.format_text(text)

            # For code blocks, the contents should not receive further
            # processing.  Whereas with quote and spoiler blocks, we
            # explicitly want Markdown formatting of the content
            # inside. This behavior is controlled by the
            # process_contents configuration flag.
            if not self.process_contents:
                text = self.processor.placeholder(text)

            processed_lines = text.split("\n")
            self.output.append("")
            self.output.extend(processed_lines)
            self.output.append("")
        self.processor.pop()

    def format_text(self, text: str) -> str:
        """Returns a formatted text.
        Subclasses should override this method.
        """
        raise NotImplementedError


def generic_handler(
    processor: "FencedBlockPreprocessor",
    output: MutableSequence[str],
    fence: str,
    lang: str | None,
    header: str | None,
    run_content_validators: bool = False,
    default_language: str | None = None,
) -> ZulipBaseHandler:
    if lang is not None:
        lang = lang.lower()
    if lang in ("quote", "quoted"):
        return QuoteHandler(processor, output, fence, default_language)
    elif lang == "math":
        return TexHandler(processor, output, fence)
    elif lang == "spoiler":
        return SpoilerHandler(processor, output, fence, header)
    else:
        return CodeHandler(processor, output, fence, lang, run_content_validators)


def parse_fence_line(line: str) -> tuple[str, str | None, str | None] | None:
    if _rust_markdown_fences is not None:
        rust_result = _rust_markdown_fences.parse_markdown_fence(line)
        if rust_result is not None:
            fence, language, header = rust_result
            if fence is None:
                return None
            return fence, language, header

    match = FENCE_RE.match(line)
    if match is None:
        return None
    return match.group("fence"), match.group("lang"), match.group("header")


def check_for_new_fence(
    processor: "FencedBlockPreprocessor",
    output: MutableSequence[str],
    line: str,
    run_content_validators: bool = False,
    default_language: str | None = None,
) -> None:
    parsed_fence = parse_fence_line(line)
    if parsed_fence is not None:
        fence, lang, header = parsed_fence
        if not lang and default_language:
            lang = default_language
        handler = generic_handler(
            processor, output, fence, lang, header, run_content_validators, default_language
        )
        processor.push(handler)
    else:
        output.append(line)


class OuterHandler(ZulipBaseHandler):
    def __init__(
        self,
        processor: "FencedBlockPreprocessor",
        output: MutableSequence[str],
        run_content_validators: bool = False,
        default_language: str | None = None,
    ) -> None:
        self.run_content_validators = run_content_validators
        self.default_language = default_language
        super().__init__(processor, output)

    @override
    def handle_line(self, line: str) -> None:
        check_for_new_fence(
            self.processor, self.output, line, self.run_content_validators, self.default_language
        )


class CodeHandler(ZulipBaseHandler):
    def __init__(
        self,
        processor: "FencedBlockPreprocessor",
        output: MutableSequence[str],
        fence: str,
        lang: str | None,
        run_content_validators: bool = False,
    ) -> None:
        self.lang = lang
        self.run_content_validators = run_content_validators
        super().__init__(processor, output, fence)

    @override
    def done(self) -> None:
        # run content validators (if any)
        if self.run_content_validators:
            validator = CODE_VALIDATORS.get(self.lang, lambda text: None)
            validator(self.lines)
        super().done()

    @override
    def format_text(self, text: str) -> str:
        return self.processor.format_code(self.lang, text)


class QuoteHandler(ZulipBaseHandler):
    def __init__(
        self,
        processor: "FencedBlockPreprocessor",
        output: MutableSequence[str],
        fence: str,
        default_language: str | None = None,
    ) -> None:
        self.default_language = default_language
        super().__init__(processor, output, fence, process_contents=True)

    @override
    def handle_line(self, line: str) -> None:
        if line.rstrip() == self.fence:
            self.done()
        else:
            check_for_new_fence(
                self.processor, self.lines, line, default_language=self.default_language
            )

    @override
    def format_text(self, text: str) -> str:
        return self.processor.format_quote(text)


class SpoilerHandler(ZulipBaseHandler):
    def __init__(
        self,
        processor: "FencedBlockPreprocessor",
        output: MutableSequence[str],
        fence: str,
        spoiler_header: str | None,
    ) -> None:
        self.spoiler_header = spoiler_header
        super().__init__(processor, output, fence, process_contents=True)

    @override
    def handle_line(self, line: str) -> None:
        if line.rstrip() == self.fence:
            self.done()
        else:
            check_for_new_fence(self.processor, self.lines, line)

    @override
    def format_text(self, text: str) -> str:
        return self.processor.format_spoiler(self.spoiler_header, text)


class TexHandler(ZulipBaseHandler):
    @override
    def format_text(self, text: str) -> str:
        return self.processor.format_tex(text)


class CodeHilite(codehilite.CodeHilite):
    def _parseHeader(self) -> None:
        parser = getattr(_rust_code_header, "parse_fenced_code_header", None)
        if (
            parser is not None
            and sys.implementation.name == "cpython"
            and sys.version_info[:2] == (3, 12)
            and type(self.src) is str
        ):
            result = parser(self.src, unicodedata.unidata_version)
            if result is not None:
                source, header = result
                if header is not None:
                    language, shebang, highlight_lines = header
                    normalizer = getattr(_rust_code_header, "normalize_fenced_code_language", None)
                    normalized = None if normalizer is None else normalizer(language)
                    self.lang = language.lower() if normalized is None else normalized
                    if self.options["linenos"] is None and shebang:
                        self.options["linenos"] = True
                    line_parser = getattr(_rust_code_header, "parse_fenced_highlight_lines", None)
                    lines = (
                        line_parser(highlight_lines)
                        if line_parser is not None and parse_hl_lines is _native_parse_hl_lines
                        else None
                    )
                    self.options["hl_lines"] = (
                        parse_hl_lines(highlight_lines) if lines is None else lines
                    )
                self.src = source
                return

        # Python-Markdown has a feature to parse-and-hide shebang
        # lines present in code blocks:
        #
        # https://python-markdown.github.io/extensions/code_hilite/#shebang-no-path
        #
        # While using shebang lines for language detection is
        # reasonable, we don't want this feature because it can be
        # really confusing when doing anything else in a one-line code
        # block that starts with `!` (which would then render as an
        # empty code block!).  So we disable the feature, by
        # overriding this function, which implements it in CodeHilite
        # upstream.

        # split text into lines
        lines = self.src.split("\n")
        # Python-Markdown pops out the first line which we are avoiding here.
        # Examine first line
        fl = lines[0]

        c = re.compile(
            r"""
            (?:(?:^::+)|(?P<shebang>^[#]!)) # Shebang or 2 or more colons
            (?P<path>(?:/\w+)*[/ ])?        # Zero or 1 path
            (?P<lang>[\w#.+-]*)             # The language
            \s*                             # Arbitrary whitespace
            # Optional highlight lines, single- or double-quote-delimited
            (hl_lines=(?P<quot>"|')(?P<hl_lines>.*?)(?P=quot))?
            """,
            re.VERBOSE,
        )
        # Search first line for shebang
        m = c.search(fl)
        if m:
            # We have a match
            try:
                self.lang = m.group("lang").lower()
            except IndexError:  # nocoverage
                self.lang = None

            if self.options["linenos"] is None and m.group("shebang"):
                # Overridable and Shebang exists - use line numbers
                self.options["linenos"] = True

            self.options["hl_lines"] = parse_hl_lines(m.group("hl_lines"))

        self.src = "\n".join(lines).strip("\n")


class FencedBlockPreprocessor(Preprocessor):
    def __init__(self, md: Markdown, run_content_validators: bool = False) -> None:
        super().__init__(md)

        self.checked_for_codehilite = False
        self.run_content_validators = run_content_validators
        self.codehilite_conf: Mapping[str, Sequence[Any]] = {}

    def push(self, handler: ZulipBaseHandler) -> None:
        self.handlers.append(handler)

    def pop(self) -> None:
        self.handlers.pop()

    def handle_lines(
        self, lines: Iterable[str], output: MutableSequence[str], default_language: str | None
    ) -> None:
        self.handlers: list[ZulipBaseHandler] = []
        self.push(OuterHandler(self, output, self.run_content_validators, default_language))
        if _rust_markdown_blocks is not None:
            lines = list(lines)
            steps = _rust_markdown_blocks.plan_markdown_blocks(lines, default_language)
            if steps is not None:
                for kind, text, language, header, block_default_language in steps:
                    handler = self.handlers[-1]
                    if kind == "close":
                        handler.done()
                    else:
                        target = output if isinstance(handler, OuterHandler) else handler.lines
                        assert text is not None
                        if kind == "open":
                            self.push(
                                generic_handler(
                                    self,
                                    target,
                                    text,
                                    language,
                                    header,
                                    self.run_content_validators
                                    if isinstance(handler, OuterHandler)
                                    else False,
                                    block_default_language,
                                )
                            )
                        else:
                            target.append(text)
                return
        for line in lines:
            self.handlers[-1].handle_line(line)

    @override
    def run(self, lines: Iterable[str]) -> list[str]:
        """Match and store Fenced Code Blocks in the HtmlStash."""

        from zerver.lib.markdown import ZulipMarkdown

        output: list[str] = []

        default_language = None
        if isinstance(self.md, ZulipMarkdown) and self.md.zulip_realm is not None:
            default_language = self.md.zulip_realm.default_code_block_language
        self.handle_lines(lines, output, default_language)

        while self.handlers:
            self.handlers[-1].done()

        # This fiddly handling of new lines at the end of our output was done to make
        # existing tests pass. Markdown is just kind of funny when it comes to new lines,
        # but we could probably remove this hack.
        if len(output) > 2 and output[-2] != "":
            output.append("")
        return output

    def format_code(self, lang: str | None, text: str) -> str:
        if lang:
            langclass = LANG_TAG.format(lang)
        else:
            langclass = ""

        prepare = getattr(_rust_code_highlight, "prepare_fenced_highlight", None)
        finish = getattr(_rust_code_highlight, "finish_fenced_highlight", None)
        if prepare is not None and finish is not None:
            enabled, highliter = prepare(self, lang, text, globals())
            if enabled:
                try:
                    code = highliter.hilite().rstrip("\n")
                except Exception:
                    logging.exception("Failed to highlight fenced code block")
                    code = CODE_WRAP.format(langclass, self._escape(text))
            else:
                code = CODE_WRAP.format(langclass, self._escape(text))
            return cast(str, finish(code, lang, globals()))

        # Check for code hilite extension
        if not self.checked_for_codehilite:
            for ext in self.md.registeredExtensions:
                if isinstance(ext, CodeHiliteExtension):
                    self.codehilite_conf = ext.config
                    break

            self.checked_for_codehilite = True

        # If config is not empty, then the codehighlite extension
        # is enabled, so we call it to highlight the code
        if self.codehilite_conf:
            highliter = CodeHilite(
                text,
                linenums=self.codehilite_conf["linenums"][0],
                guess_lang=self.codehilite_conf["guess_lang"][0],
                css_class=self.codehilite_conf["css_class"][0],
                style=self.codehilite_conf["pygments_style"][0],
                use_pygments=self.codehilite_conf["use_pygments"][0],
                lang=lang or None,
                noclasses=self.codehilite_conf["noclasses"][0],
                # By default, the Pygments PHP lexers won't highlight
                # code without a `<?php` marker at the start of the
                # code block, which is undesired in the common case of
                # pasting a snippet of PHP code rather than whole
                # file. The `startinline` option overrides this
                # behavior for PHP-descended languages and has no
                # effect on other lexers.
                #
                # See https://pygments.org/docs/lexers/#lexers-for-php-and-related-languages
                startinline=True,
            )

            try:
                code = highliter.hilite().rstrip("\n")
            except Exception:
                logging.exception("Failed to highlight fenced code block")
                code = CODE_WRAP.format(langclass, self._escape(text))
        else:
            code = CODE_WRAP.format(langclass, self._escape(text))

        # To support our "view in playground" feature, the frontend
        # needs to know what Pygments language was used for
        # highlighting this code block.  We record this in a data
        # attribute attached to the outer `pre` element.
        # Unfortunately, the pygments API doesn't offer a way to add
        # this, so we need to do it in a post-processing step.
        if lang:
            div_tag = lxml.html.fromstring(code)

            # For the value of our data element, we get the lexer
            # subclass name instead of directly using the language,
            # since that canonicalizes aliases (Eg: `js` and
            # `javascript` will be mapped to `JavaScript`).
            try:
                code_language = find_lexer_class_by_name(lang).name
            except ClassNotFound:
                # If there isn't a Pygments lexer by this name, we
                # still tag it with the user's data-code-language
                # value, since this allows hooking up a "playground"
                # for custom "languages" that aren't known to Pygments.
                code_language = lang

            div_tag.attrib["data-code-language"] = code_language
            code = lxml.html.tostring(div_tag, encoding="unicode")
        return code

    def format_quote(self, text: str) -> str:
        format_native = getattr(_rust_markdown_fences, "format_markdown_fenced_quote", None)
        if format_native is not None and type(text) is str:
            quoted = format_native(text)
            if quoted is not None:
                return cast(str, quoted)
        paragraphs = text.split("\n")
        quoted_paragraphs = []
        for paragraph in paragraphs:
            lines = paragraph.split("\n")
            quoted_paragraphs.append("\n".join("> " + line for line in lines))
        return "\n".join(quoted_paragraphs)

    def format_spoiler(self, header: str | None, text: str) -> str:
        format_native = getattr(_rust_markdown_fences, "format_fenced_spoiler", None)
        if format_native is not None:
            return cast(str, format_native(self, header, text))
        output = []
        header_div_open_html = '<div class="spoiler-block"><div class="spoiler-header">'
        end_header_start_content_html = '</div><div class="spoiler-content" aria-hidden="true">'
        footer_html = "</div></div>"

        output.append(self.placeholder(header_div_open_html))
        if header is not None:
            output.append(header)
        output.append(self.placeholder(end_header_start_content_html))
        output.append(text)
        output.append(self.placeholder(footer_html))
        return "\n\n".join(output)

    def format_tex(self, text: str) -> str:
        handler = getattr(_rust_tex_handlers, "render_markdown_display_tex", None)
        if handler is not None:
            return cast(str, handler(text, globals()))
        paragraphs = text.split("\n\n")
        tex_paragraphs = []
        for paragraph in paragraphs:
            html = render_tex(paragraph, is_inline=False)
            if html is not None:
                tex_paragraphs.append(html)
            else:
                tex_paragraphs.append('<span class="tex-error">' + escape(paragraph) + "</span>")
        return "\n\n".join(tex_paragraphs)

    def placeholder(self, code: str) -> str:
        return self.md.htmlStash.store(code)

    def _escape(self, txt: str) -> str:
        """basic html escaping"""
        escape_code = getattr(_rust_code_highlight, "escape_fenced_code", None)
        if escape_code is not None and type(txt) is str:
            result = escape_code(txt)
            if result is not None:
                return cast(str, result)
        txt = txt.replace("&", "&amp;")
        txt = txt.replace("<", "&lt;")
        txt = txt.replace(">", "&gt;")
        txt = txt.replace('"', "&quot;")
        return txt


class UnclosedFenceFinder(FencedBlockPreprocessor):
    """Finds the fenced blocks that content leaves open, using the same
    handlers that parse fenced blocks when rendering a message. Blocks
    that content closes aren't used, so this skips formatting code and
    math blocks, which is slow.
    """

    @override
    def format_code(self, lang: str | None, text: str) -> str:
        return text

    @override
    def format_tex(self, text: str) -> str:
        return text

    def get_unclosed_fences(self, content: str, default_language: str | None) -> list[str]:
        # Markdown normalizes whitespace before parsing fenced blocks.
        lines = ZulipNormalizeWhitespace(self.md).run(content.split("\n"))
        self.handle_lines(lines, [], default_language)
        return [handler.fence for handler in reversed(self.handlers) if handler.fence is not None]


def close_unclosed_fences(content: str, default_language: str | None = None) -> str:
    """Appends a closing fence for each fenced block that content leaves
    open, so that the blocks can't swallow text placed after content.
    default_language is the realm's default code block language, used
    for fences that don't name a language.
    """
    close_fences = getattr(_rust_markdown_blocks, "close_unclosed_markdown_fences", None)
    if (
        close_fences is not None
        and type(content) is str
        and (default_language is None or type(default_language) is str)
    ):
        result = close_fences(content, default_language)
        if result is not None:
            return cast(str, result)
    unclosed_fences = UnclosedFenceFinder(Markdown()).get_unclosed_fences(content, default_language)
    return "\n".join([content, *unclosed_fences])


def makeExtension(*args: Any, **kwargs: Any) -> FencedCodeExtension:
    return FencedCodeExtension(kwargs)


if __name__ == "__main__":
    import doctest

    doctest.testmod()
