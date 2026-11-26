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
    parser = argparse.ArgumentParser(description="Compare unclosed Markdown fence completion")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--random-cases", type=int, default=30000)
    parser.add_argument("--mutant-reverse-order", action="store_true")
    args = parser.parse_args()
    django.setup()
    from zerver.lib.markdown import fenced_code

    extension = import_module("_zulip_backend")
    rng = random.Random(962851)
    checked = accepted = delegated = 0
    differences = []

    def check(source: str, language: str | None) -> None:
        nonlocal checked, accepted, delegated
        expected = fenced_code.close_unclosed_fences(source, language)
        result = extension.close_unclosed_markdown_fences(source, language)
        try:
            supported = len(source.encode("utf-8")) <= 8 * 1024 * 1024
            if language is not None:
                language.encode("utf-8")
        except UnicodeEncodeError:
            supported = False
        assert (result is not None) == supported, repr((source[:100], language))
        accepted += supported
        delegated += not supported
        actual = result if supported else expected
        if args.mutant_reverse_order:
            suffix = actual[len(source) :]
            actual = source + ("\n" + "\n".join(reversed(suffix.split("\n")[1:])) if suffix else "")
        checked += 1
        if actual != expected:
            differences.append(
                {
                    "source": repr(source[:100]),
                    "language": language,
                    "expected_suffix": expected[len(source) :],
                    "actual_suffix": actual[len(source) :],
                }
            )

    lines = [
        "",
        "```",
        "~~~~",
        "```quote",
        "````quoted",
        "~~~spoiler title",
        "~~~math",
        "```py",
        "```QUOTE",
        "```{.quote}",
        "```spoiler عنوان",
        "```quote\t",
        "```\tpy",
        " ```quote",
        "\t```",
        "```\u3000",
        "````",
        "body &<> فارسی",
        "\x02```\x03",
        "```\x1c",
        "```\u2028",
        "~~~",
    ]
    languages = [
        None,
        "",
        "quote",
        "QuOtEd",
        "spoiler",
        "math",
        "python",
        "İΣ",
        "ΟΣ",
        "Kotlin",
        "فارسی",
        "\ud800",
    ]
    with (
        patch.object(fenced_code, "_rust_markdown_blocks", None),
        patch.object(fenced_code, "_rust_prepared_close_fences", None),
        patch.object(fenced_code, "_rust_markdown_fences", None),
        patch("zerver.lib.markdown.whitespace._rust_markdown_whitespace", None),
    ):
        for source in [
            "",
            "\ud800\n```",
            "x" * (8 * 1024 * 1024),
            "x" * (8 * 1024 * 1024 - 4) + "\n```",
            "x" * (8 * 1024 * 1024 + 1),
        ]:
            check(source, None)
        for parts in itertools.product(lines, repeat=3):
            check("\n".join(parts), rng.choice(languages))
            if len(differences) >= 10:
                break
        for _ in range(args.random_cases if not differences else 0):
            separator = rng.choice(["\n", "\r", "\r\n", "\u2028"])
            source = separator.join(rng.choices(lines, k=rng.randrange(30)))
            check(source, rng.choice(languages))
        if not differences:
            for value in range(0x110000):
                char = chr(value)
                if char.isspace() or value < 128 or value in range(0xD800, 0xE000):
                    check(f"```quote\n~~~py\ntext\n~~~{char}\n```", None)
    report = {
        "seed": 962851,
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
