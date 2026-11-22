# ruff: noqa: INP001
import argparse
import hashlib
import json
import random
from importlib import import_module
from itertools import product
from pathlib import Path
from types import SimpleNamespace


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare successful KaTeX HTTP text decoding")
    parser.add_argument("--seed", type=int, default=962855)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mutant-ascii-strip", action="store_true")
    args = parser.parse_args()
    extension = import_module("_zulip_backend")
    rng = random.Random(args.seed)
    checked = errors = 0
    differences: list[dict[str, object]] = []

    def check(body: bytes) -> None:
        nonlocal checked, errors
        try:
            expected: object = ("returned", body.decode().strip())
        except UnicodeDecodeError as error:
            expected = ("raised", type(error).__name__, error.args)
            errors += 1
        try:
            result = extension.render_katex_response(
                SimpleNamespace(status_code=200, content=body), {}
            )
            if args.mutant_ascii_strip:
                result = body.decode().strip(" \t\r\n\v\f")
            actual: object = ("returned", result)
        except UnicodeDecodeError as error:
            actual = ("raised", type(error).__name__, error.args)
        checked += 1
        if actual != expected and len(differences) < 10:
            differences.append(
                {"body": repr(body), "expected": repr(expected), "actual": repr(actual)}
            )

    # Check the complete Unicode scalar set at both boundaries and inside text.
    for codepoint in range(0x110000):
        if 0xD800 <= codepoint <= 0xDFFF:
            continue
        character = chr(codepoint)
        check((character + "\r\n<math>" + character + "</math>\r" + character).encode())
        if len(differences) == 10:
            break
    if not differences:
        for size in range(3):
            for values in product(range(256), repeat=size):
                check(bytes(values))
        for _ in range(20_000):
            check(rng.randbytes(rng.randrange(300)))
        for body in [
            b"",
            b"\xef\xbb\xbf x \xef\xbb\xbf",
            b"\x00\r\n",
            b"x" * (8 * 1024 * 1024 + 1),
        ]:
            check(body)
    assert extension.__file__ is not None
    repository = Path(__file__).resolve().parents[2]
    sources = [
        Path(__file__).resolve(),
        repository / "rust-backend/src/python_tex.rs",
        repository / "rust-backend/src/draft_text.rs",
        Path(extension.__file__),
        *Path(extension.__file__).parent.glob("*.so"),
    ]
    report = {
        "seed": args.seed,
        "checked": checked,
        "exceptions": errors,
        "differences": differences,
        "mutant_ascii_strip": args.mutant_ascii_strip,
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "source_sha256"}))
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
