# ruff: noqa: INP001
import argparse
import hashlib
import json
import random
import re
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch
from xml.etree.ElementTree import Element

import django
from markdown.util import HtmlStash


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare live Python and Rust TeX handlers")
    parser.add_argument("--cases", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=962843)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mutant-plain-errors", action="store_true")
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    django.setup()
    from zerver.lib import markdown
    from zerver.lib.markdown import fenced_code

    extension = import_module("_zulip_backend")
    rng = random.Random(args.seed)
    bodies = [
        "",
        "x",
        "<script>&\"'",
        "\ud800",
        "\udfff",
        "فارسی α 😀",
        "\n\n",
        "first\n\nsecond\n\n\n\nlast\n\n",
        "$$\\invalid{<script>}$$",
        "\x02klzzwxh:0000\x03",
        "&amp; :smile: @**all** **bold**",
        "\r\n\r\n",
        "x" * 100_000,
    ]
    alphabet = [*"x_$*<&>\"'\\ \n\r\t", "α", "ی", "😀", "\ud800", "\udfff", "\x00"]
    bodies.extend("".join(rng.choices(alphabet, k=rng.randrange(150))) for _ in range(args.cases))
    differences: list[dict[str, object]] = []
    checked = inline = display = errors = 0

    def snapshot(value: object) -> object:
        if isinstance(value, Element):
            return (
                value.tag,
                list(value.attrib.items()),
                value.text,
                type(value.text).__name__,
                value.tail,
                [snapshot(child) for child in value],
            )
        return (type(value).__name__, value)

    for index, body in enumerate(bodies):
        for is_inline in (True, False):
            offset = rng.choice([0, 1, 9999, 1_000_000])
            failure_at = rng.choice([None, None, None, 0, 1, 3])
            expected: object = None
            for native in (False, True):
                stash = HtmlStash()
                stash.store("previous")
                stash.html_counter = offset
                blocks = stash.rawHtmlBlocks
                calls: list[tuple[str, bool]] = []

                def render(
                    text: str,
                    *,
                    is_inline: bool,
                    calls: list[tuple[str, bool]] = calls,
                    failure_at: int | None = failure_at,
                    index: int = index,
                ) -> str | None:
                    position = len(calls)
                    calls.append((text, is_inline))
                    if position == failure_at:
                        raise ValueError("controlled renderer failure", position)
                    choice = (sum(map(ord, text)) + index) % 5
                    return [None, "", "<i>math &amp;</i>", "\ud800", text][choice]

                with (
                    patch.object(markdown, "_rust_tex_handlers", extension if native else None),
                    patch.object(fenced_code, "_rust_tex_handlers", extension if native else None),
                    patch.object(markdown, "render_tex", render),
                    patch.object(fenced_code, "render_tex", render),
                ):
                    outcome: tuple[object, ...]
                    try:
                        if is_inline:
                            pattern = cast(
                                markdown.Tex, SimpleNamespace(md=SimpleNamespace(htmlStash=stash))
                            )
                            matched = re.match(r"(?P<body>.*)", body, re.DOTALL)
                            assert matched is not None
                            result = markdown.Tex.handleMatch(pattern, matched)
                        else:
                            processor = cast(fenced_code.FencedBlockPreprocessor, SimpleNamespace())
                            result = fenced_code.FencedBlockPreprocessor.format_tex(processor, body)
                        if native and args.mutant_plain_errors and isinstance(result, Element):
                            result.text = str(result.text)
                        outcome = ("returned", snapshot(result))
                    except Exception as error:
                        outcome = ("raised", type(error).__name__, error.args)
                actual = (
                    outcome,
                    calls,
                    stash.html_counter,
                    stash.rawHtmlBlocks[:],
                    stash.rawHtmlBlocks is blocks,
                )
                if not native:
                    expected = actual
                elif actual != expected:
                    differences.append(
                        {
                            "index": index,
                            "inline": is_inline,
                            "body": body,
                            "expected": expected,
                            "actual": actual,
                        }
                    )
            checked += 1
            inline += is_inline
            display += not is_inline
            errors += outcome[0] == "raised"
            if len(differences) >= 10:
                break
        if len(differences) >= 10:
            break

    repository = Path(__file__).resolve().parents[2]
    assert extension.__file__ is not None
    sources = [
        Path(__file__).resolve(),
        repository / "rust-backend/src/markdown_tex_handlers.rs",
        repository / "rust-backend/src/lib.rs",
        repository / "rust-backend/src/python.rs",
        Path(markdown.__file__),
        Path(fenced_code.__file__),
        Path(extension.__file__),
        Path(sys.executable).resolve(),
    ]
    report = {
        "seed": args.seed,
        "checked": checked,
        "inline": inline,
        "display": display,
        "exceptions": errors,
        "differences": differences,
        "mutant_plain_errors": args.mutant_plain_errors,
        "python": sys.version,
        "source_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources
        },
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "source_sha256"}))
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
