# ruff: noqa: INP001
import argparse
import hashlib
import itertools
import json
import os
import random
import sys
import unicodedata
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path


def cases(length: int, random_cases: int, seed: int, unicode_scalars: bool) -> Iterator[str]:
    yield from [
        "",
        "$$x$$",
        "📝 $$α_1$$ : $$later$$\n",
        r"$$x\$$",
        r"$$x\$$$",
        r"$$x\$y$$",
        r"$$\$$",
        r"$$x\$$$$ : $$y$$",
        "a$$x$$",
        "²$$x$$",
        "$$x$$سلام",
        "$$x$$²",
        "\u0301$$x$$\u0301",
        "$$bad$ : $$good$$",
        "$$x\n$$y$$",
        "$$\r$$",
        "$$x\0y$$\n",
    ]
    for size in range(length + 1):
        yield from ("".join(value) for value in itertools.product("$\\\n_a ", repeat=size))
    rng = random.Random(seed)
    atoms = [
        "$$",
        "$$$",
        r"\$",
        "\\",
        "x",
        "_",
        " ",
        "\n",
        "\r",
        "\0",
        "<script>",
        "\u0301",
        "²",
        "سلام",
        "😊",
        "𞓐",
        "\x02klzzwxh:0001\x03",
    ]
    for _ in range(random_cases):
        data = "".join(rng.choices(atoms, k=rng.randrange(1, 40)))
        yield data
        yield "$$" + data + "$$\n"
    if unicode_scalars:
        for point in range(0x110000):
            if 0xD800 <= point <= 0xDFFF:
                continue
            character = chr(point)
            yield character + "$$x$$"
            yield "$$x$$" + character
            yield "$$" + character + "$$"
            yield "$$a" + character + "$$"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare native TeX syntax to the live Pattern.")
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--max-length", type=int, default=6)
    parser.add_argument("--cases", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=962842)
    parser.add_argument("--unicode-scalars", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not 0 <= args.max_length <= 9 or args.cases < 0:
        parser.error("use a maximum length from 0 through 9 and a nonnegative case count")
    if any(name.startswith("ZULIP_RUST_") and value == "1" for name, value in os.environ.items()):
        parser.error("unset Rust feature flags to load the independent Python reference")
    sys.path.insert(0, str(args.repository.resolve()))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")
    import django

    django.setup()
    from zerver.lib.markdown import ZulipMarkdown

    extension = import_module("_zulip_backend")
    md = ZulipMarkdown(linkifiers=[], linkifiers_key=0, email_gateway=False)
    reference = md.inlinePatterns["tex"].compiled_re
    checked = matched = differences = 0
    examples: list[dict[str, object]] = []
    for data in cases(args.max_length, args.cases, args.seed, args.unicode_scalars):
        expected = reference.match(data)
        actual = extension.find_markdown_tex(data, unicodedata.unidata_version)
        checked += 1
        matched += expected is not None
        if actual != (None if expected is None else expected.groups(),):
            differences += 1
            if len(examples) < 20:
                examples.append(
                    {
                        "data": data,
                        "expected": None if expected is None else expected.groups(),
                        "actual": actual,
                    }
                )
        if checked % 500000 == 0:
            print(f"checked={checked} matched={matched} differences={differences}", flush=True)
    root = args.repository.resolve()
    sources = [
        "rust-backend/src/markdown_tex_syntax.rs",
        "rust-backend/src/markdown_autolink_data.rs",
        "rust-backend/src/python_tex.rs",
        "rust-backend/src/lib.rs",
        "rust-backend/src/python.rs",
        "rust-backend/Cargo.toml",
        "rust-backend/Cargo.lock",
        "rust-backend/tests/check_tex_syntax_parity.py",
        "rust-backend/tests/test_tex_syntax_bridge.py",
        "zerver/lib/markdown/__init__.py",
        "zerver/lib/markdown/tex_syntax.py",
        "zerver/lib/markdown/native_inline.py",
        "zerver/tests/test_markdown_tex_syntax.py",
    ]
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources}
    native = import_module("_zulip_backend._zulip_backend")
    assert native.__file__ is not None
    for path in [Path(native.__file__), Path(sys.executable).resolve()]:
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {
        "python": sys.version,
        "unicode_version": unicodedata.unidata_version,
        "pattern": reference.pattern,
        "flags": reference.flags,
        "seed": args.seed,
        "max_length": args.max_length,
        "random_cases": args.cases,
        "unicode_scalars": args.unicode_scalars,
        "checked": checked,
        "matched": matched,
        "missed": checked - matched,
        "differences": differences,
        "examples": examples,
        "hashes": hashes,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "hashes"}))
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
