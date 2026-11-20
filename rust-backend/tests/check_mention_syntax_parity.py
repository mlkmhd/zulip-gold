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


def patterns(source: Path) -> dict[str, re.Pattern[str]]:
    names = {"BEFORE_MENTION_ALLOWED_REGEX", "MENTIONS_RE", "USER_GROUP_MENTIONS_RE"}
    module = ast.parse(source.read_text())
    assignments = {
        target.id: node.value
        for node in module.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in names
    }
    assert len(assignments) == len(names)
    boundary = ast.literal_eval(assignments["BEFORE_MENTION_ALLOWED_REGEX"])
    assert isinstance(boundary, str)
    result = {}
    for kind, name in [("user", "MENTIONS_RE"), ("group", "USER_GROUP_MENTIONS_RE")]:
        call = assignments[name]
        assert isinstance(call, ast.Call) and len(call.args) == 1 and not call.keywords
        expression = call.args[0]
        assert isinstance(expression, ast.JoinedStr)
        parts = []
        for part in expression.values:
            if isinstance(part, ast.Constant):
                assert isinstance(part.value, str)
                parts.append(part.value)
            else:
                assert isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name)
                assert part.value.id == "BEFORE_MENTION_ALLOWED_REGEX"
                assert part.conversion == -1 and part.format_spec is None
                parts.append(boundary)
        result[kind] = re.compile("".join(parts))
    return result


def cases(seed: int, count: int, all_boundaries: bool) -> Iterator[tuple[str, int]]:
    for value in [
        "",
        "@**** @**ok**",
        "@**open @**nested**",
        "@**one* @**two**",
        "@_**نام|42** @*گروه*",
        "🌍 @_**نام|42** @*گروه*",
        "@**a\nb** @_**a** @*group*",
        "@**|42\n**",
        "@__**wrong** @**ok**",
        "@**unfinished",
        "@*one* @_*two*",
    ]:
        for start in range(len(value.encode()) + 2):
            yield value, start
    for length in range(6):
        for letters in itertools.product(["@", "*", "_", "a", " ", "\n"], repeat=length):
            yield "".join(letters), 0
    if all_boundaries:
        for point in range(0x110000):
            if 0xD800 <= point <= 0xDFFF:
                continue
            before = chr(point)
            for syntax in ["@**x**", "@*x*"]:
                yield before + syntax, len(before.encode())
    rng = random.Random(seed)
    atoms = [
        "a",
        "@",
        "*",
        "_",
        "|",
        " ",
        "\n",
        "\r",
        "\x1c",
        "\u0085",
        "\u2003",
        "'",
        '"',
        "(",
        "{",
        "[",
        "/",
        "<",
        "]",
        ")",
        "نام",
        "🌍",
        "\x02klzzwxh:0000\x03",
        "@**",
        "**",
        "@_*",
        "@*",
        "topic",
    ]
    for _ in range(count):
        value = "".join(rng.choices(atoms, k=rng.randrange(40)))
        start = rng.randrange(len(value.encode()) + 2)
        yield value, start


def expected(pattern: re.Pattern[str], text: str, start: int) -> str:
    encoded = text.encode()
    try:
        prefix = encoded[:start].decode()
    except UnicodeDecodeError:
        return "invalid"
    if start > len(encoded):
        return "invalid"
    match = pattern.search(text, len(prefix))
    if match is None:
        return "miss"
    return "\t".join(
        [
            "match",
            str(len(text[: match.start()].encode())),
            str(len(text[: match.end()].encode())),
            match.group("match").encode().hex(),
            str(int(match.group("silent") == "_")),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare native mention candidates with live regexes"
    )
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--reference-only", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--cases", type=int, default=100000)
    parser.add_argument("--seed", type=int, default=962827)
    parser.add_argument("--all-boundaries", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    if args.probe is None and not args.reference_only:
        parser.error("--probe is required unless --reference-only is selected")
    root = Path(__file__).resolve().parents[2]
    reference = args.source or root / "zerver/lib/mention.py"
    regexes = patterns(reference)
    corpus = iter(cases(args.seed, args.cases, args.all_boundaries))
    checked = 0
    failures: list[dict[str, str | int]] = []
    mismatches = 0
    counts = {"match": 0, "miss": 0, "invalid": 0}
    digest = hashlib.sha256()
    while batch := list(itertools.islice(corpus, 2048)):
        inputs = [(kind, text, start) for text, start in batch for kind in regexes]
        wanted_lines = [expected(regexes[kind], text, start) for kind, text, start in inputs]
        if args.reference_only:
            lines = wanted_lines
        else:
            result = subprocess.run(
                [str(args.probe.resolve())],
                input="".join(
                    f"{kind}\t{start}\t{text.encode().hex()}\n" for kind, text, start in inputs
                ),
                capture_output=True,
                text=True,
                check=True,
            )
            lines = result.stdout.splitlines()
            assert len(lines) == len(inputs), result.stderr
        for (kind, text, start), actual, wanted in zip(inputs, lines, wanted_lines, strict=True):
            digest.update(
                json.dumps([kind, text, start, wanted], ensure_ascii=False).encode() + b"\n"
            )
            checked += 1
            counts[actual.split("\t")[0]] += 1
            if wanted != actual:
                mismatches += 1
                if len(failures) < 10:
                    failures.append(
                        {
                            "kind": kind,
                            "text": text,
                            "start": start,
                            "expected": wanted,
                            "actual": actual,
                        }
                    )
    sources = [Path(__file__), reference]
    if not args.reference_only:
        sources.extend(
            [
                root / "rust-backend/src/markdown_mention_syntax.rs",
                root / "rust-backend/src/markdown_mentions.rs",
                root / "rust-backend/examples/mention_syntax.rs",
                args.probe,
            ]
        )
    report = {
        "python": platform.python_version(),
        "seed": args.seed,
        "all_boundaries": args.all_boundaries,
        "reference_only": args.reference_only,
        "reference_sha256": digest.hexdigest(),
        "cases": checked,
        "counts": counts,
        "mismatches": mismatches,
        "failures": failures,
        "sha256": {
            str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources
        },
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ["cases", "counts", "mismatches"]}))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
