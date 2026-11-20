# ruff: noqa: INP001
import argparse
import hashlib
import inspect
import itertools
import json
import random
import subprocess
from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path

from markdown.inlinepatterns import IMAGE_LINK_RE, LINK_RE, LinkInlineProcessor
from typing_extensions import override


class Reference(LinkInlineProcessor):
    def __init__(self, mode: str) -> None:
        super().__init__(LINK_RE if mode == "link" else IMAGE_LINK_RE)
        self.inputs: list[str] = []

    @override
    def unescape(self, text: str) -> str:
        self.inputs.append(text)
        return text


def reference(data: str, start: int, mode: str) -> dict[str, object] | str | None:
    processor = Reference(mode)
    match = processor.getCompiledRegExp().match(data, start)
    if match is None:
        return None
    text, index, handled = processor.getText(data, match.end())
    if not handled:
        return None
    _, title, end, handled = processor.getLink(data, index)
    if not handled:
        return None
    if end < 0:
        return "invalid-end"
    return {
        "text": text,
        "href": processor.inputs[-1],
        "title": processor.inputs[0] if title is not None else None,
        "end": len(data[:end].encode()),
    }


def decoded(data: str, line: str) -> dict[str, object] | str | None:
    if line == "-":
        return None
    if line == "invalid-end":
        return line
    text_start, text_end, href_start, href_end, title_start, title_end, end = map(int, line.split())
    encoded = data.encode()
    assert 0 <= text_start <= text_end < href_start <= href_end <= end <= len(encoded)
    assert (title_start, title_end) == (-1, -1) or (href_end < title_start <= title_end < end)
    return {
        "text": encoded[text_start:text_end].decode(),
        "href": encoded[href_start:href_end].decode(),
        "title": None if title_start < 0 else encoded[title_start:title_end].decode().strip(),
        "end": end,
    }


def cases(seed: int, count: int, exhaustive_depth: int) -> Iterator[tuple[str, int]]:
    fixed = [
        "[x](url)",
        "[]()",
        "![x](url)",
        "[x] (url)",
        "[[x]](a(b(c))) tail",
        '[x](link"notitle) tail',
        '[x](url"(tail',
        '[x](url\'"title")',
        "[x](<a<b>)",
        "[x](<a> junk)",
        "[x](< >)",
        '[x](url "title"\t)',
        "[x](\u0002klzzwxh:0000\u0003 '\u000234\u0003')",
        "[x](javascript:alert(1))",
        "[x](<a\nb> 'title\nlines')",
        "[x](url '\"quoted\"') trailing",
        '[x](url"(tail) rest',
        "[x](" + "(" * 10000 + "a" + ")" * 10001,
        "[" * 10000 + "x" + "]" * 10000 + "(url)",
    ]
    for data in fixed:
        yield data, 0
        yield "پیش 🌍 " + data, len("پیش 🌍 ")
    for point in range(0x110000):
        space = chr(point)
        if not space.isspace():
            continue
        for data in (
            f"[x]({space}url{space})",
            f"[x]({space}<{space}url{space}>{space}'title'{space})",
            f"[x](url 'title'{space})",
        ):
            yield data, 0
    alphabet = "()'\"<> a"
    for length in range(exhaustive_depth + 1):
        for characters in itertools.product(alphabet, repeat=length):
            body = "".join(characters)
            yield "[x](" + body, 0
            yield "[x](" + body + ") tail", 0
    rng = random.Random(seed)
    atoms = [
        "a",
        " ",
        "(",
        ")",
        "'",
        '"',
        "<",
        ">",
        "[",
        "]",
        "\\",
        "\n",
        "\t",
        "\r",
        "🌍",
        "فارسی",
        "\u001c",
        "\u0085",
        "\u2003",
        "\u200b",
        "\u0000",
        "\u0002klzzwxh:0000\u0003",
        "\u000234\u0003",
    ]
    for _ in range(count):
        label = "".join(rng.choices(atoms, k=rng.randrange(12)))
        destination = "".join(rng.choices(atoms, k=rng.randrange(40)))
        prefix = rng.choice(["", "!", "🌍 ", "پیش "])
        data = prefix + "[" + label + "](" + destination + rng.choice(["", ")", ") tail"])
        yield data, len(prefix)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare raw link syntax with Python-Markdown")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--mode", choices=["link", "image"], default="link")
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=962821)
    parser.add_argument("--exhaustive-depth", type=int, choices=range(7), default=4)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    inputs = list(cases(args.seed, args.cases, args.exhaustive_depth))
    if args.mode == "image":
        inputs = [(data[:start] + "!" + data[start:], start) for data, start in inputs]
    requests = "".join(
        f"{args.mode}\t{len(data[:start].encode())}\t{data.encode().hex()}\n"
        for data, start in inputs
    )
    output = subprocess.run(
        [str(args.probe.resolve())], input=requests, text=True, capture_output=True, check=True
    )
    lines = output.stdout.splitlines()
    assert len(lines) == len(inputs), (len(lines), len(inputs), output.stderr)
    counts = {"matched": 0, "no_match": 0, "invalid_end": 0}
    failures = []
    for (data, start), line in zip(inputs, lines, strict=True):
        expected = reference(data, start, args.mode)
        actual = decoded(data, line)
        counts[
            "no_match"
            if actual is None
            else "invalid_end"
            if actual == "invalid-end"
            else "matched"
        ] += 1
        if actual != expected:
            failures.append({"data": data, "start": start, "expected": expected, "actual": actual})
    root = Path(__file__).resolve().parents[2]
    sources = [
        Path(__file__).resolve(),
        root / "rust-backend/src/markdown_link_syntax.rs",
        root / "rust-backend/examples/link_syntax.rs",
        args.probe.resolve(),
        Path(inspect.getfile(LinkInlineProcessor)).resolve(),
    ]
    report = {
        "markdown_version": version("Markdown"),
        "mode": args.mode,
        "seed": args.seed,
        "random_cases": args.cases,
        "exhaustive_depth": args.exhaustive_depth,
        "checked": len(inputs),
        "counts": counts,
        "hashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        "failures": failures,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "hashes"}))
    assert all(counts.values()), "Corpus must exercise links, missing links, and invalid ends"
    assert not failures, f"{len(failures)} mismatches; see {args.report}"


if __name__ == "__main__":
    main()
