import locale
import logging
import os
import subprocess
import sys
from importlib import import_module
from typing import Any, cast

import lxml.html
import requests
from django.conf import settings

from zerver.lib.outgoing_http import OutgoingSession
from zerver.lib.storage import static_path

_rust_markdown_tex = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_MARKDOWN_TEX") == "1" else None
)
_subprocess_check_output = subprocess.check_output
_subprocess_popen = subprocess.Popen

_rust_raw_latex = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_RAW_LATEX") == "1" else None
)

_rust_katex_response = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_KATEX_RESPONSE") == "1" else None
)


class KatexSession(OutgoingSession):
    def __init__(self, **kwargs: Any) -> None:
        # We set a very short timeout because these requests are
        # expected to be quite fast (milliseconds) and blocking on
        # this affects message rendering performance.
        super().__init__(role="katex", timeout=0.5, **kwargs)


def render_tex(tex: str, is_inline: bool = True) -> str | None:
    r"""Render a TeX string into HTML using KaTeX

    Returns the HTML string, or None if there was some error in the TeX syntax

    Keyword arguments:
    tex -- Text string with the TeX to render
           Don't include delimiters ('$$', '\[ \]', etc.)
    is_inline -- Boolean setting that indicates whether the render should be
                 inline (i.e. for embedding it in text) or not. The latter
                 will show the content centered, and in the "expanded" form
                 (default True)
    """

    if settings.KATEX_SERVER:
        request = getattr(_rust_katex_response, "request_katex_service", None)
        try:
            if request is not None:
                resp = request(tex, is_inline, globals())
            else:
                resp = KatexSession().post(
                    # We explicitly disable the Smokescreen proxy for this
                    # call, since it intentionally connects to localhost.
                    # This is safe because the host is explicitly fixed, and
                    # the port is pulled from our own configuration.
                    f"http://localhost:{settings.KATEX_SERVER_PORT}/",
                    data={
                        "content": tex,
                        "is_display": "false" if is_inline else "true",
                        "shared_secret": settings.SHARED_SECRET,
                    },
                    proxies={"http": ""},
                )
        except requests.exceptions.Timeout:
            logging.warning("KaTeX rendering service timed out with %d byte long input", len(tex))
            return None
        except requests.exceptions.RequestException as e:
            logging.warning("KaTeX rendering service failed: %s", type(e).__name__)
            return None

        response_handler = getattr(_rust_katex_response, "render_katex_response", None)
        if response_handler is not None:
            return cast(str | None, response_handler(resp, globals()))
        if resp.status_code == 200:
            return resp.content.decode().strip()
        elif resp.status_code == 400:
            return None
        else:
            logging.warning(
                "KaTeX rendering service failed: (%s) %s", resp.status_code, resp.content.decode()
            )
            return None

    locate = getattr(_rust_markdown_tex, "locate_katex_cli", None)
    if locate is not None:
        exists, katex_path = locate(globals())
    else:
        katex_path = (
            static_path("webpack-bundles/katex-cli.js")
            if settings.PRODUCTION
            else os.path.join(settings.DEPLOY_ROOT, "node_modules/katex/cli.js")
        )
        exists = os.path.isfile(katex_path)
    if not exists:
        logging.error("Cannot find KaTeX for latex rendering!")
        return None
    rust_render = getattr(_rust_markdown_tex, "run_katex_cli", None)
    native_cli_allowed = getattr(_rust_markdown_tex, "katex_native_cli_allowed", None)
    if rust_render is not None and (
        native_cli_allowed(globals())
        if native_cli_allowed is not None
        else (
            sys.platform == "linux"
            and not sys.flags.warn_default_encoding
            and subprocess.check_output is _subprocess_check_output
            and subprocess.Popen is _subprocess_popen
        )
    ):
        select_encoding = getattr(_rust_markdown_tex, "select_katex_encoding", None)
        encoding = (
            select_encoding(globals())
            if select_encoding is not None
            else "utf-8"
            if sys.flags.utf8_mode
            else locale.getencoding()
        )
        encode = getattr(_rust_markdown_tex, "encode_katex_input", None)
        try:
            stdin = encode(tex, encoding) if encode is not None else tex.encode(encoding)
            success, stdout = rust_render(katex_path, stdin, is_inline)
        except NotImplementedError:
            pass
        else:
            finish = getattr(_rust_markdown_tex, "finish_katex_cli", None)
            if finish is not None:
                return cast(str | None, finish(success, stdout, encoding))
            # Text-mode subprocesses decode even unsuccessful output and normalize
            # newlines before the caller checks the exit status.
            rendered = stdout.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
            return rendered.strip() if success else None
    command = ["node", katex_path]
    if not is_inline:
        command.extend(["--display-mode"])
    try:
        stdout = subprocess.check_output(command, input=tex, stderr=subprocess.DEVNULL, text=True)
        # stdout contains a newline at the end
        return stdout.strip()
    except subprocess.CalledProcessError:
        return None


def change_katex_to_raw_latex(fragment: lxml.html.HtmlElement) -> None:
    convert = getattr(_rust_raw_latex, "change_katex_to_raw_latex", None)
    if convert is not None:
        convert(fragment, globals())
        return
    # Selecting the <span> elements with class 'katex'
    katex_spans = fragment.xpath("//span[@class='katex']")

    # Iterate through 'katex_spans' and replace with a new <span> having LaTeX text.
    for katex_span in katex_spans:
        latex_text = katex_span.xpath(".//annotation[@encoding='application/x-tex']")[0].text
        # We store 'tail' to insert them back as the replace operation removes it.
        tail = katex_span.tail
        latex_span = lxml.html.Element("span")
        latex_span.text = f"$${latex_text}$$"
        katex_span.getparent().replace(katex_span, latex_span)
        latex_span.tail = tail
