# ruff: noqa: INP001
import argparse
import itertools
import json
import random
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import django


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare unused Markdown fence selection")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--unicode", action="store_true")
    parser.add_argument("--random-cases", type=int, default=20000)
    parser.add_argument("--mutant-short-fence", action="store_true")
    args = parser.parse_args()
    django.setup()
    from zerver.lib.markdown import fenced_code

    extension = import_module("_zulip_backend")
    rng = random.Random(962850)
    checked = accepted = delegated = 0
    differences = []

    def check(source: str) -> None:
        nonlocal checked, accepted, delegated
        expected = fenced_code.get_unused_fence(source)
        result = extension.get_unused_markdown_fence(source)
        assert extension.choose_fenced_quote_delimiter(source) == expected, repr(source[:100])
        try:
            supported = len(source.encode("utf-8")) <= 8 * 1024 * 1024
        except UnicodeEncodeError:
            supported = False
        assert (result is not None) == supported, repr(source[:100])
        accepted += supported
        delegated += not supported
        actual = result if supported else expected
        if args.mutant_short_fence and len(actual) > 3:
            actual = actual[:-1]
        checked += 1
        if actual != expected:
            differences.append(
                {
                    "source": repr(source[:100]),
                    "expected_length": len(expected),
                    "actual_length": len(actual),
                }
            )

    with (
        patch.object(fenced_code, "_rust_markdown_fences", None),
        patch.object(fenced_code, "_rust_quote_delimiter", None),
    ):
        for source in [
            "",
            "\ud800\n```",
            "`" * (8 * 1024 * 1024),
            "`" * (8 * 1024 * 1024 + 1),
            "x" * (8 * 1024 * 1024) + "\n```",
        ]:
            check(source)
        for length in range(7):
            for parts in itertools.product(" `\n\r\t~a", repeat=length):
                check("".join(parts))
                if len(differences) >= 10:
                    break
            if len(differences) >= 10:
                break
        for _ in range(args.random_cases if not differences else 0):
            source = "".join(rng.choices(" `\n\r\t~aفارسی😀\u2028\ud800", k=rng.randrange(300)))
            check(source)
        if args.unicode and not differences:
            for value in range(0x110000):
                char = chr(value)
                for source in [f"{char}```", f"  {char}````", f"``{char}``"]:
                    check(source)
    report = {
        "seed": 962850,
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
