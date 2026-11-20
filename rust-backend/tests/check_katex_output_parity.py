# ruff: noqa: INP001

"""Compare CLI output handling with Python without executing a subprocess."""

import argparse
import hashlib
import json
import random
from importlib import import_module
from pathlib import Path

_zulip_backend = import_module("_zulip_backend")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=962846)
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mutant-newlines", action="store_true")
    args = parser.parse_args()
    randomizer = random.Random(args.seed)
    checked = errors = 0
    differences: list[dict[str, object]] = []

    def compare(output: bytes, encoding: str, success: bool) -> None:
        nonlocal checked, errors
        expected: tuple[object, ...]
        actual: tuple[object, ...]
        try:
            decoded = output.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
            expected = ("returned", decoded.strip() if success else None)
        except Exception as error:
            expected = ("raised", type(error).__name__, error.args)
        try:
            result = _zulip_backend.finish_katex_cli(success, output, encoding)
            if args.mutant_newlines and result is not None:
                result = result.replace("\n", "\r")
            actual = ("returned", result)
        except Exception as error:
            actual = ("raised", type(error).__name__, error.args)
        checked += 1
        errors += expected[0] == "raised"
        if actual != expected and len(differences) < 10:
            differences.append(
                {
                    "output": repr(output),
                    "encoding": encoding,
                    "success": success,
                    "expected": repr(expected),
                    "actual": repr(actual),
                }
            )

    for codepoint in range(0x110000):
        if not 0xD800 <= codepoint <= 0xDFFF:
            output = (chr(codepoint) + "a\r\nb\rc" + chr(codepoint)).encode()
            compare(output, "utf-8", True)
    for _ in range(args.cases):
        output = randomizer.randbytes(randomizer.randrange(100))
        for encoding in ["utf-8", "UTF8", "ascii", "latin-1", "utf-16", "utf-32"]:
            compare(output, encoding, randomizer.choice([True, False]))
    for output in [b"", b"\r", b"a\r\rb", b"\r\n\r", b"\\ud800"]:
        for encoding in ["utf_8", "utf-8-sig", "unicode-escape", "zulip-invalid-encoding"]:
            for success in [False, True]:
                compare(output, encoding, success)
    sources = [
        "rust-backend/src/markdown_tex.rs",
        "rust-backend/src/python_tex.rs",
        "rust-backend/src/python.rs",
        "zerver/lib/tex.py",
        str(Path(__file__)),
    ]
    report = {
        "seed": args.seed,
        "comparisons": checked,
        "exceptions": errors,
        "differences": differences,
        "mutant": args.mutant_newlines,
        "source_sha256": {
            source: hashlib.sha256(Path(source).read_bytes()).hexdigest() for source in sources
        },
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "source_sha256"}))
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
