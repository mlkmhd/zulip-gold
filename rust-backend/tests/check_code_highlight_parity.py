# ruff: noqa: INP001
import argparse
import json
import random
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import django
from markdown import Markdown
from markdown.extensions.codehilite import CodeHiliteExtension


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare fenced code highlighting paths")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=1500)
    parser.add_argument("--mutant-language", action="store_true")
    args = parser.parse_args()
    django.setup()
    from zerver.lib.markdown import fenced_code

    extension = import_module("_zulip_backend")
    rng = random.Random(962847)
    languages = [
        None,
        "",
        "js",
        "javascript",
        "python",
        "php",
        "text",
        "<custom>",
        "فارسی",
        "\ud800",
    ]
    bodies = [
        "",
        "<script>&\"'",
        "<?php echo 1;",
        "#!/bin/python\nprint(1)",
        "\n\n",
        "فارسی 😀",
        "\ud800",
    ]
    differences = []
    errors = checked = 0
    for index in range(args.cases + len(languages) * len(bodies)):
        language = (
            languages[index // len(bodies)]
            if index < len(languages) * len(bodies)
            else rng.choice(languages)
        )
        body = (
            bodies[index % len(bodies)]
            if index < len(languages) * len(bodies)
            else "".join(rng.choices("abc <>\n\r\t&'\"فارسی😀", k=rng.randrange(100)))
        )
        enabled = rng.choice([False, True])
        use_pygments = rng.choice([False, True])
        noclasses = rng.choice([False, True])
        expected: object = None
        for native in [None, extension]:
            extensions = (
                [
                    CodeHiliteExtension(
                        use_pygments=use_pygments, noclasses=noclasses, guess_lang=False
                    )
                ]
                if enabled
                else []
            )
            processor = fenced_code.FencedBlockPreprocessor(Markdown(extensions=extensions))
            with patch.object(fenced_code, "_rust_code_highlight", native):
                try:
                    result: tuple[object, ...] = ("returned", processor.format_code(language, body))
                except Exception as error:
                    result = ("raised", type(error).__name__, error.args)
            if native and args.mutant_language and result[0] == "returned":
                result = (
                    "returned",
                    str(result[1]).replace(
                        'data-code-language="JavaScript"', 'data-code-language="js"'
                    ),
                )
            actual = (result, processor.checked_for_codehilite, dict(processor.codehilite_conf))
            if native is None:
                expected = actual
            elif actual != expected:
                differences.append(
                    {
                        "index": index,
                        "language": repr(language),
                        "body": repr(body),
                        "expected": repr(expected),
                        "actual": repr(actual),
                    }
                )
        checked += 1
        errors += result[0] == "raised"
        if len(differences) >= 10:
            break
    report = {"seed": 962847, "checked": checked, "exceptions": errors, "differences": differences}
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=True))
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
