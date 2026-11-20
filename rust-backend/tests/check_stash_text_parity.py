# ruff: noqa: INP001
import argparse
import hashlib
import json
import platform
import random
import subprocess
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree

Node = tuple[str | None, str | None, list[int]]
Stashed = int | str


def cases(seed: int, count: int) -> Iterator[tuple[list[Node], list[Stashed]]]:
    yield [], []
    yield [], ["", "فارسی 🌍", "\u0002klzzwxh:0000\u0003"]
    yield [(None, "excluded", [])], [0]
    yield [("a", "excluded", [1, 1]), ("b", "c", [])], [0, 1]
    yield [("a", "excluded", [1]), (None, "tail", [2]), ("b", "c", [])], [0]
    rng = random.Random(seed)
    atoms = ["", "a", "فارسی", "🌍", "\n", "\t", "<>&", "\u0002klzzwxh:0000\u0003"]

    def text() -> str | None:
        if rng.randrange(4) == 0:
            return None
        return "".join(rng.choices(atoms, k=rng.randrange(5)))

    for _ in range(count):
        nodes: list[Node] = []
        size = rng.randrange(1, 35)
        for index in range(size):
            children = (
                rng.choices(range(index + 1, size), k=rng.randrange(4)) if index + 1 < size else []
            )
            nodes.append((text(), text(), children))
        stash: list[Stashed] = [rng.randrange(size) for _ in range(rng.randrange(1, 8))]
        stash.extend([text() or "", "\u0002klzzwxh:0000\u0003"])
        yield nodes, stash


def encode(nodes: list[Node], stash: list[Stashed]) -> str:
    fields = [str(len(nodes))]
    for text, tail, children in nodes:
        fields.extend(
            [
                "-" if text is None else text.encode().hex(),
                "-" if tail is None else tail.encode().hex(),
                ",".join(map(str, children)),
            ]
        )
    fields.extend(
        f"n:{value}" if isinstance(value, int) else f"t:{value.encode().hex()}" for value in stash
    )
    return "\t".join(fields) + "\n"


def expected(nodes: list[Node], stash: list[Stashed]) -> list[str]:
    elements = [ElementTree.Element("span") for _ in nodes]
    for element, (text, tail, children) in zip(elements, nodes, strict=True):
        element.text = text
        element.tail = tail
        element.extend(elements[child] for child in children)
    return [
        "".join(elements[value].itertext()) if isinstance(value, int) else value for value in stash
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare native stash snapshots with ElementTree")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=962823)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    corpus = list(cases(args.seed, args.cases))
    failures = []
    accepted = 0
    for start in range(0, len(corpus), 512):
        batch = corpus[start : start + 512]
        process = subprocess.run(
            [str(args.probe.resolve())],
            input="".join(encode(nodes, stash) for nodes, stash in batch),
            text=True,
            capture_output=True,
            check=True,
        )
        lines = process.stdout.splitlines()
        assert len(lines) == len(batch), process.stderr
        for offset, ((nodes, stash), line) in enumerate(zip(batch, lines, strict=True)):
            wanted = expected(nodes, stash)
            fields = line.split("\t")
            actual = None
            if fields[0] == "ok":
                actual = [bytes.fromhex(value).decode() for value in fields[1:]] if stash else []
                accepted += 1
            if actual != wanted:
                failures.append(
                    {
                        "case": start + offset,
                        "nodes": nodes,
                        "stash": stash,
                        "expected": wanted,
                        "actual": actual,
                    }
                )
    root = Path(__file__).resolve().parents[1]
    sources = [
        Path(__file__),
        root / "src/markdown_native_inline.rs",
        root / "examples/stash_text.rs",
        Path(ElementTree.__file__),
        args.probe,
    ]
    report = {
        "python": platform.python_version(),
        "seed": args.seed,
        "cases": len(corpus),
        "accepted": accepted,
        "mismatches": len(failures),
        "failures": failures[:10],
        "sha256": {
            str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources
        },
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ["cases", "accepted", "mismatches"]}))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
