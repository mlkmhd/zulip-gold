# ruff: noqa: INP001
import argparse
import copy
import hashlib
import json
import random
import sys
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import django
import lxml.html


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare KaTeX-to-LaTeX tree mutations")
    parser.add_argument("--cases", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=962844)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mutant-drop-tails", action="store_true")
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    django.setup()
    from zerver.lib import tex

    extension = import_module("_zulip_backend")
    convert: Callable[[lxml.html.HtmlElement], object] = tex.change_katex_to_raw_latex
    rng = random.Random(args.seed)
    fixed = [
        "<div/>",
        '<span class="katex"><annotation encoding="application/x-tex">x</annotation></span>',
        (
            '<p>before <span class="katex"><annotation encoding="application/x-tex">'
            "&lt;script&gt;&amp; فارسی 😀</annotation></span> after</p>"
        ),
        (
            '<div><span class="katex"><annotation encoding="application/x-tex"/></span>'
            '<span class="katex">missing</span></div>'
        ),
        (
            '<div><span class="katex extra">unchanged</span><span class="katex">'
            '<annotation encoding="application/x-tex"><b>child</b></annotation></span></div>'
        ),
        (
            '<div><span class="katex"><span class="katex">'
            '<annotation encoding="application/x-tex">inner</annotation></span>'
            '<annotation encoding="application/x-tex">outer</annotation></span></div>'
        ),
    ]
    roots = [lxml.html.fromstring(html) for html in fixed]
    texts = [None, "", "x", "<script>&\"'", "α فارسی 😀", "\n\r\t", "$$None$$", "\\frac{1}{2}"]
    classes = [None, "katex", "katex", "katex extra", "katex-display", " katex", "KaTeX"]
    encodings = [None, "application/x-tex", "application/x-tex", "other", "APPLICATION/X-TEX"]
    differences: list[dict[str, object]] = []
    checked = errors = replaced = 0
    factory = lxml.html.Element

    for index in range(args.cases + len(roots)):
        if index < len(roots):
            root = roots[index]
        else:
            root = factory("div")
            parents = [root]
            for _ in range(rng.randrange(12)):
                span = factory(rng.choice(["span", "span", "span", "div", "{urn:test}span"]))
                value = rng.choice(classes)
                if value is not None:
                    span.set("class", value)
                span.text, span.tail = rng.choice(texts), rng.choice(texts)
                rng.choice(parents).append(span)
                parents.append(span)
                for _ in range(rng.randrange(4)):
                    annotation = factory(rng.choice(["annotation", "annotation", "other"]))
                    encoding = rng.choice(encodings)
                    if encoding is not None:
                        annotation.set("encoding", encoding)
                    annotation.text, annotation.tail = rng.choice(texts), rng.choice(texts)
                    if rng.randrange(3) == 0:
                        annotation.append(factory("b"))
                        annotation[0].text = "child text"
                    span.append(annotation)
        nodes = list(root.iter())
        selected = rng.randrange(len(nodes))
        failure_at = rng.choice([None, None, None, 0, 1, 2])
        expected: object = None
        for native in [False, True]:
            tree = copy.deepcopy(root)
            original = list(tree.iter())
            fragment = original[selected]
            calls: list[str] = []

            def create(
                tag: str,
                calls: list[str] = calls,
                failure_at: int | None = failure_at,
            ) -> lxml.html.HtmlElement:
                position = len(calls)
                calls.append(tag)
                if position == failure_at:
                    raise ValueError("controlled element failure", position)
                return factory(tag)

            outcome: tuple[object, ...]
            with (
                patch.object(tex, "_rust_raw_latex", extension if native else None),
                patch.object(lxml.html, "Element", create),
            ):
                try:
                    result = convert(fragment)
                    if native and args.mutant_drop_tails:
                        for node in tree.iter("span"):
                            if node.text and node.text.startswith("$$"):
                                node.tail = None
                    outcome = ("returned", result)
                except Exception as error:
                    outcome = ("raised", type(error).__name__, error.args)
            actual = (
                outcome,
                calls,
                lxml.html.tostring(tree),
                [
                    (
                        lxml.html.tostring(node),
                        next(
                            (i for i, old in enumerate(original) if old is node.getparent()), None
                        ),
                    )
                    for node in original
                ],
                next(tree.iter()) is tree,
            )
            if not native:
                expected = actual
            elif actual != expected:
                differences.append(
                    {
                        "index": index,
                        "input": str(lxml.html.tostring(root)),
                        "selected": selected,
                        "expected": repr(expected),
                        "actual": repr(actual),
                    }
                )
        checked += 1
        errors += outcome[0] == "raised"
        replaced += len(calls)
        if len(differences) >= 10:
            break

    repository = Path(__file__).resolve().parents[2]
    assert extension.__file__ is not None
    sources = [
        Path(__file__).resolve(),
        repository / "rust-backend/src/python_tex.rs",
        repository / "rust-backend/src/python.rs",
        repository / "rust-backend/src/lib.rs",
        Path(tex.__file__),
        Path(extension.__file__),
        Path(sys.executable).resolve(),
    ]
    report = {
        "seed": args.seed,
        "checked": checked,
        "exceptions": errors,
        "factory_calls": replaced,
        "differences": differences,
        "mutant_drop_tails": args.mutant_drop_tails,
        "python": sys.version,
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "source_sha256"}))
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
