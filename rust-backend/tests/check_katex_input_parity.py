# ruff: noqa: INP001

"""Compare KaTeX stdin encoding with Python without starting any process."""

import argparse
import hashlib
import json
import random
import sys
from importlib import import_module
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=962854)
    parser.add_argument("--cases", type=int, default=20_000)
    parser.add_argument("--unicode-scalars", action="store_true")
    parser.add_argument("--mutant-newlines", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    extension = import_module("_zulip_backend")
    randomizer = random.Random(args.seed)
    checked = errors = 0
    differences: list[dict[str, object]] = []

    def compare(content: str, encoding: str) -> None:
        nonlocal checked, errors
        try:
            expected: tuple[object, ...] = ("returned", content.encode(encoding))
        except (UnicodeError, LookupError) as error:
            expected = ("raised", type(error).__name__, error.args)
        try:
            result = extension.encode_katex_input(content, encoding)
            if args.mutant_newlines:
                result = result.replace(b"\r\n", b"\n")
            actual = ("returned", result)
        except (UnicodeError, LookupError) as error:
            actual = ("raised", type(error).__name__, error.args)
        checked += 1
        errors += expected[0] == "raised"
        if actual != expected and len(differences) < 10:
            differences.append(
                {
                    "content": repr(content),
                    "encoding": encoding,
                    "expected": repr(expected),
                    "actual": repr(actual),
                }
            )

    encodings = [
        "utf-8",
        "UTF-8",
        "utf8",
        "UTF8",
        "utf_8",
        "utf-8-sig",
        "latin-1",
        "ascii",
        "utf-16",
        "utf-32",
        "unicode-escape",
        "invalid-codec",
    ]
    for content in ["", "\r\n\0", "فارسی 😀", "\ud800", "x\udfffy"]:
        for encoding in encodings:
            compare(content, encoding)
    if args.unicode_scalars:
        for codepoint in range(0x110000):
            compare(chr(codepoint) + "x\r\n\0", "utf-8")
            if len(differences) == 10:
                break
    for _ in range(args.cases):
        content = "".join(
            chr(randomizer.randrange(0x110000)) for _ in range(randomizer.randrange(50))
        )
        for encoding in encodings:
            compare(content, encoding)
        if len(differences) == 10:
            break

    root = Path(__file__).resolve().parents[2]
    native = import_module("_zulip_backend._zulip_backend")
    assert native.__file__ is not None
    sources = [
        root / "rust-backend/src/python_tex.rs",
        root / "rust-backend/src/python.rs",
        root / "zerver/lib/tex.py",
        Path(__file__).resolve(),
        Path(native.__file__),
        Path(sys.executable).resolve(),
    ]
    report = {
        "seed": args.seed,
        "checked": checked,
        "exceptions": errors,
        "differences": differences,
        "mutant_newlines": args.mutant_newlines,
        "python": sys.version,
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "source_sha256"}))
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
