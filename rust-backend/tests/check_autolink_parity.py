# ruff: noqa: INP001
import argparse
import ast
import hashlib
import itertools
import json
import platform
import random
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from tlds import tld_set


def patterns(source: Path) -> tuple[list[str], list[str], dict[bool, re.Pattern[str]]]:
    module = ast.parse(source.read_text())
    namespace: dict[str, object] = {"tld_set": tld_set}
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("web_link_regex", "list_of_tlds"):
            # These are the checked-out pure reference helpers, with no imports.
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)  # noqa: S102
    schemes = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "auto_linked_schemes"
            for target in node.targets
        )
    )
    from collections.abc import Callable

    list_tlds = cast(Callable[[], list[str]], namespace["list_of_tlds"])
    build = cast(Callable[[str, str, bool], str], namespace["web_link_regex"])
    tlds = list_tlds()
    regexes = {
        files: re.compile(
            "^(.*?)" + build("|".join(tlds), "|".join(schemes), files) + "(.*?)$",
            re.DOTALL | re.VERBOSE,
        )
        for files in (False, True)
    }
    return tlds, schemes, regexes


def cases(seed: int, count: int, unicode_scalars: bool) -> Iterator[tuple[str, bool]]:
    for text in [
        "",
        "example.com",
        "https://x?",
        "https://x??",
        "https://x?.",
        "https://x?)",
        "https://x:_:@:??\u0085",
        "https://x?()",
        "a@b.com/x example.com",
        "https://x/a(b(c))tail!",
        "file:///tmp/a\nb.txt",
        "[https://x]",
        "bitcoin:1BoatSLRHtKNngkdXEeobR76b53LETtpyT",
        "https://例.com/نام",
    ]:
        for files in (False, True):
            yield text, files
    for depth in range(11):
        for prefix, suffix in itertools.product(
            ["https://x/", "https://x?"], ["", ".", ")", " tail"]
        ):
            yield prefix + "(" * depth + "x" + ")" * depth + suffix, False
    for before, after in itertools.product(" !?:,([<'\"é🙂\n\u0301", repeat=2):
        yield before + "https://x?()." + after, False
    rng = random.Random(seed)
    atoms = [
        "a",
        "1",
        ".",
        ":",
        "/",
        "?",
        "#",
        "@",
        "(",
        ")",
        '"',
        "'",
        "[",
        "]",
        " ",
        "\n",
        "\t",
        "\x1c",
        "\u0085",
        "é",
        "🙂",
        "_",
        "-",
        ";",
        ",",
        "!",
        ">",
        "\\",
    ]
    for _ in range(count):
        text = "".join(rng.choices(atoms, k=rng.randrange(8)))
        text += rng.choice(
            [
                "https://x",
                "a.com",
                "a@example.com",
                "obsidian://open",
                "file:///tmp/a",
                "https://user:pass@host:8080",
                "bitcoin:1BoatSLRHtKNngkdXEeobR76b53LETtpyT",
            ]
        )
        text += "".join(rng.choices(atoms, k=rng.randrange(30)))
        text += rng.choice(["", " example.org", " https://other/?.."])
        yield text, bool(rng.randrange(2))
    if unicode_scalars:
        for point in range(0x110000):
            if 0xD800 <= point <= 0xDFFF:
                continue
            char = chr(point)
            for text in [char + "https://x", "https://x/" + char, char + ".com", "a@b." + char]:
                yield text, False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare native autolink byte ranges with the Python reference."
    )
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=100000)
    parser.add_argument("--seed", type=int, default=962840)
    parser.add_argument("--unicode-scalars", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    root = Path(__file__).resolve().parents[2]
    source = root / "zerver/lib/markdown/__init__.py"
    tlds, schemes, regexes = patterns(source)
    header = ",".join(tlds) + "\n" + ",".join(schemes) + "\n"
    corpus = iter(cases(args.seed, args.cases, args.unicode_scalars))
    checked = matches = misses = 0
    failures = []
    digest = hashlib.sha256()
    while batch := list(itertools.islice(corpus, 8192)):
        result = subprocess.run(
            [str(args.probe.resolve())],
            input=header
            + "".join(f"{int(files)}\t{text.encode().hex()}\n" for text, files in batch),
            capture_output=True,
            text=True,
            check=True,
        )
        actual = result.stdout.splitlines()
        assert len(actual) == len(batch)
        for (text, files), got in zip(batch, actual, strict=True):
            match = regexes[files].match(text)
            if match is None:
                expected = "miss"
                misses += 1
            else:
                expected = f"{len(text[: match.start('url')].encode())}\t{len(text[: match.end('url')].encode())}"
                matches += 1
            digest.update(f"{int(files)}\t{text.encode().hex()}\t{expected}\n".encode())
            checked += 1
            if got != expected:
                failures.append({"text": text, "files": files, "expected": expected, "actual": got})
                if len(failures) >= 10:
                    break
        if failures:
            break
        if checked % 65536 == 0:
            print(f"Checked {checked} ranges", flush=True)
    sources = [
        source,
        Path(__file__),
        args.probe,
        root / "rust-backend/src/markdown_autolinks.rs",
        root / "rust-backend/src/markdown_autolink_data.rs",
        root / "rust-backend/examples/autolinks.rs",
        root / "rust-backend/tools/generate_markdown_autolink_data.py",
    ]
    args.report.write_text(
        json.dumps(
            {
                "python": platform.python_version(),
                "seed": args.seed,
                "random_cases": args.cases,
                "unicode_scalars": args.unicode_scalars,
                "checked": checked,
                "matches": matches,
                "misses": misses,
                "reference_digest": digest.hexdigest(),
                "failures": failures,
                "hashes": {
                    str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sources
                },
            },
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    )
    print(f"{checked} checked; {matches} matches; {misses} misses; {len(failures)} differences")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
