# ruff: noqa: INP001
import argparse
import hashlib
import inspect
import json
import random
import subprocess
from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

from check_link_syntax_parity import cases
from markdown import Markdown
from markdown.inlinepatterns import IMAGE_LINK_RE, LINK_RE, LinkInlineProcessor
from markdown.treeprocessors import InlineProcessor


def inputs(seed: int, count: int) -> Iterator[tuple[str, int]]:
    yield from cases(seed, count, 4)
    rng = random.Random(seed)
    atoms = [
        '"',
        "'",
        " ",
        "\n",
        "\t",
        "\u001c",
        "\u0085",
        "\u00a0",
        "🌍",
        "فارسی",
        "\u0002klzzwxh:0000\u0003",
        "\u0002klzzwxh:0001\u0003",
        "\u0002klzzwxh:0002\u0003",
        "\u0002klzzwxh:1\u0003",
        "\u0002klzzwxh:9999\u0003",
        "\u0002klzzwxh:۴۲\u0003",
        "\u0002klzzwxh:bad",
        "\u000234\u0003",
    ]
    for _ in range(count):
        href = "".join(rng.choices(atoms, k=rng.randrange(10)))
        title = "".join(rng.choices(atoms, k=rng.randrange(12)))
        yield f"[label](<{href}> '{title}')", 0
    for point in range(0x110000):
        char = chr(point)
        if char.isspace():
            yield f"[x](<{char}\u0002klzzwxh:0000\u0003{char}> '{char}title{char}')", 0
    for title in ["", '"', "'", '""', "''", '"mismatch', '"a\n\tb"']:
        yield f"[x](url '{title}')", 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare native link text preparation")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=962822)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    tree = Element("span")
    tree.text = '"a\n'
    SubElement(tree, "em").text = "nested 🌍"
    tree[0].tail = '\tend"'
    tree.tail = "EXCLUDED ROOT TAIL"
    snapshots: dict[str, dict[str, str | Element] | None] = {
        "absent": None,
        "empty": {},
        "rich": {
            "0000": " \u2003example.com\u2003 ",
            "0001": tree,
            "0002": "\u0002klzzwxh:0000\u0003",
            "1": "distinct unpadded key",
        },
    }
    corpus = list(inputs(args.seed, args.cases))
    counts = {"matched": 0, "no_match": 0, "invalid_end": 0}
    failures = []
    checked = 0
    for mode in ["link", "image"]:
        for name, stash in snapshots.items():
            md = Markdown()
            if stash is None:
                md.treeprocessors.deregister("inline")
            else:
                inline = md.treeprocessors["inline"]
                assert isinstance(inline, InlineProcessor)
                inline.stashed_nodes = stash
            processor = LinkInlineProcessor(LINK_RE if mode == "link" else IMAGE_LINK_RE, md)
            flattened = (
                {}
                if stash is None
                else {
                    key: value if isinstance(value, str) else "".join(value.itertext())
                    for key, value in stash.items()
                }
            )
            suffix = "".join(
                f"\t{key.encode().hex()}\t{value.encode().hex()}"
                for key, value in flattened.items()
            )
            for offset in range(0, len(corpus), 4096):
                batch = corpus[offset : offset + 4096]
                if mode == "image":
                    batch = [(data[:start] + "!" + data[start:], start) for data, start in batch]
                requests = "".join(
                    f"{mode}\t{len(data[:start].encode())}\t{data.encode().hex()}\t"
                    f"{'absent' if stash is None else 'present'}{suffix}\n"
                    for data, start in batch
                )
                output = subprocess.run(
                    [str(args.probe.resolve())],
                    input=requests,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                lines = output.stdout.splitlines()
                assert len(lines) == len(batch), output.stderr
                for (data, start), line in zip(batch, lines, strict=True):
                    match = processor.getCompiledRegExp().match(data, start)
                    expected: list[str | int | None] | str | None = None
                    if match is not None:
                        label, index, handled = processor.getText(data, match.end())
                        if handled:
                            href, title, end, handled = processor.getLink(data, index)
                            if handled:
                                expected = (
                                    "invalid-end"
                                    if end < 0
                                    else [label, href, title, len(data[:end].encode())]
                                )
                    actual: list[str | int | None] | str | None
                    if line in ["-", "invalid-end"]:
                        actual = None if line == "-" else line
                    else:
                        label_hex, href_hex, title_hex, end_string = line.split("\t")
                        actual = [
                            bytes.fromhex(label_hex).decode(),
                            bytes.fromhex(href_hex).decode(),
                            None if title_hex == "-" else bytes.fromhex(title_hex).decode(),
                            int(end_string),
                        ]
                    counts[
                        "no_match"
                        if actual is None
                        else "invalid_end"
                        if actual == "invalid-end"
                        else "matched"
                    ] += 1
                    checked += 1
                    if actual != expected:
                        failures.append(
                            {
                                "mode": mode,
                                "stash": name,
                                "data": data,
                                "expected": expected,
                                "actual": actual,
                            }
                        )
    root = Path(__file__).resolve().parents[2]
    sources = [
        Path(__file__).resolve(),
        Path(__file__).with_name("check_link_syntax_parity.py"),
        root / "rust-backend/src/markdown_link_syntax.rs",
        root / "rust-backend/examples/link_text.rs",
        args.probe.resolve(),
        Path(inspect.getfile(LinkInlineProcessor)).resolve(),
    ]
    report = {
        "markdown_version": version("Markdown"),
        "seed": args.seed,
        "random_cases_per_family": args.cases,
        "checked": checked,
        "counts": counts,
        "hashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        "failures": failures,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "hashes"}))
    assert all(counts.values()), "Missing coverage of parser outcomes"
    assert not failures, f"{len(failures)} mismatches; see {args.report}"


if __name__ == "__main__":
    main()
