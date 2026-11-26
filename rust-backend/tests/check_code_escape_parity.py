# ruff: noqa: INP001
import argparse
import itertools
import json
import random
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import django
from markdown import Markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare fenced code HTML escaping paths")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--unicode", action="store_true")
    parser.add_argument("--random-cases", type=int, default=20000)
    parser.add_argument("--mutant-unescaped-quotes", action="store_true")
    args = parser.parse_args()
    django.setup()
    from zerver.lib.markdown import fenced_code

    extension = import_module("_zulip_backend")
    processor = fenced_code.FencedBlockPreprocessor(Markdown())
    rng = random.Random(962849)
    checked = accepted = delegated = 0
    differences = []

    def check(source: str) -> None:
        nonlocal checked, accepted, delegated
        expected = processor._escape(source)
        result = extension.escape_fenced_code(source)
        exact = extension.escape_exact_fenced_code(source)
        assert exact == expected, repr(source[:100])
        if expected is source:
            assert exact is source, repr(source[:100])
        try:
            supported = len(source.encode("utf-8")) <= 8 * 1024 * 1024
        except UnicodeEncodeError:
            supported = False
        assert (result is not None) == supported, repr(source[:100])
        accepted += supported
        delegated += not supported
        actual = result if supported else expected
        if args.mutant_unescaped_quotes:
            actual = actual.replace("&quot;", '"')
        checked += 1
        if actual != expected:
            differences.append(
                {
                    "source": repr(source[:100]),
                    "expected": repr(expected[:200]),
                    "actual": repr(actual[:200]),
                }
            )

    with (
        patch.object(fenced_code, "_rust_code_highlight", None),
        patch.object(fenced_code, "_rust_code_escape", None),
    ):
        for source in [
            "",
            '<script a="x">\'&amp;</script>',
            '\ud800<&"',
            "😀" * (2 * 1024 * 1024) + '"',
            "x" * (8 * 1024 * 1024 - 1) + '"',
        ]:
            check(source)
        for length in range(7):
            for parts in itertools.product("<&>\"'a", repeat=length):
                check("".join(parts))
                if len(differences) >= 10:
                    break
            if len(differences) >= 10:
                break
        for _ in range(args.random_cases if not differences else 0):
            source = "".join(
                rng.choices("abc <>&\"'\n\r\t;#فارسی😀\ud800\x00\u2028", k=rng.randrange(300))
            )
            check(source)
        if args.unicode and not differences:
            for value in range(0x110000):
                char = chr(value)
                check(f"<{char}&\"{char}'>")
    report = {
        "seed": 962849,
        "checked": checked,
        "accepted": accepted,
        "delegated": delegated,
        "differences": differences,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=True))
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
