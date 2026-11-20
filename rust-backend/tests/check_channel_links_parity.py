# ruff: noqa: INP001
import __future__

import argparse
import ast
import hashlib
import itertools
import json
import platform
import random
import re
import subprocess
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from xml.etree.ElementTree import Element

KINDS = ("channel", "topic", "message")
Handler = Callable[[object, object, str], tuple[Element, int, int]]


class AtomicString(str):
    __slots__ = ()


def reference(root: Path) -> tuple[dict[str, re.Pattern[str]], dict[str, Handler]]:
    source = root / "zerver/lib/markdown/__init__.py"
    module = ast.parse(source.read_text())
    boundary = next(
        ast.literal_eval(node.value)
        for node in ast.parse((root / "zerver/lib/mention.py").read_text()).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "BEFORE_LINK_PRODUCING_MENTION_ALLOWED_REGEX"
            for t in node.targets
        )
    )
    assert isinstance(boundary, str)
    topic_display = next(
        ast.literal_eval(node.value)
        for node in ast.parse((root / "zerver/lib/topic_link_util.py").read_text()).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "TOPIC_LINK_SYNTAX_FOR_DISPLAY"
            for t in node.targets
        )
    )
    assert isinstance(topic_display, str)
    regexes = {}
    for kind, name in zip(
        KINDS,
        ("STREAM_LINK_REGEX", "STREAM_TOPIC_LINK_REGEX", "STREAM_TOPIC_MESSAGE_LINK_REGEX"),
        strict=True,
    ):
        value = next(
            node.value
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        )
        assert isinstance(value, ast.JoinedStr)
        parts = []
        for part in value.values:
            if isinstance(part, ast.Constant):
                assert isinstance(part.value, str)
                parts.append(part.value)
            else:
                assert isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name)
                assert part.value.id == "BEFORE_LINK_PRODUCING_MENTION_ALLOWED_REGEX"
                assert part.conversion == -1 and part.format_spec is None
                parts.append(boundary)
        regexes[kind] = re.compile("".join(parts), re.VERBOSE | re.DOTALL)
    functions: list[ast.stmt] = []
    for kind, name in zip(
        KINDS, ("StreamPattern", "StreamTopicPattern", "StreamTopicMessagePattern"), strict=True
    ):
        cls = next(
            node for node in module.body if isinstance(node, ast.ClassDef) and node.name == name
        )
        method = next(
            node
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name == "handleMatch"
        )
        method.name = kind
        method.decorator_list = []
        functions.append(method)
    url_nodes: list[ast.stmt] = []
    url_source = root / "zerver/lib/url_encoding.py"
    for node in ast.parse(url_source.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "encode_hash_component",
            "encode_channel",
        }:
            url_nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "hash_replacements" for t in node.targets
        ):
            url_nodes.append(node)
    assert len(url_nodes) == 3 and len(functions) == 3
    scope: dict[str, object] = {
        "urllib": urllib,
        "_rust_url_fragments": None,
        "Element": Element,
        "markdown": SimpleNamespace(util=SimpleNamespace(AtomicString=AtomicString)),
        "channel_links": SimpleNamespace(channel_link=lambda *args, **kwargs: None),
        "ChannelTopicInfo": lambda channel, topic: (channel, topic),
        "Message": SimpleNamespace(EMPTY_TOPIC_FALLBACK_NAME="<empty & نام>"),
        "TOPIC_LINK_SYNTAX_FOR_DISPLAY": topic_display,
    }
    # Execute only selected definitions from the trusted reference checkout.
    exec(  # noqa: S102
        compile(
            ast.Module(body=url_nodes + functions, type_ignores=[]),
            str(source),
            "exec",
            flags=__future__.annotations.compiler_flag,
        ),
        scope,
    )
    return regexes, {kind: cast(Handler, scope[kind]) for kind in KINDS}


def syntax_cases(seed: int, count: int, all_boundaries: bool) -> Iterator[tuple[str, int]]:
    for value in [
        "",
        "#**** #**ok**",
        "#**open #**nested>topic**",
        "[#**no**] <#**yes**",
        "#**x>@0001**",
        "#**x>topic@۱٢3**",
        "🌍 #** نام >a>b@x@۰۰1**",
        "#**x>t@1\n** #**good>t@2**",
        "#**x>t@**",
        "#**>t@1**",
        "#**x>**",
    ]:
        for start in range(len(value.encode()) + 2):
            yield value, start
    for length in range(7):
        for chars in itertools.product(["#", "*", ">", "@", "1", " "], repeat=length):
            yield "".join(chars), 0
    for point in range(0x110000):
        if 0xD800 <= point <= 0xDFFF:
            continue
        char = chr(point)
        if all_boundaries:
            yield char + "#**x>t@1**", len(char.encode())
        if char.isdecimal():
            yield f"#**x>t@0{char}**", 0
    rng = random.Random(seed)
    atoms = [
        "#**",
        "**",
        "#",
        "*",
        ">",
        "@",
        "1",
        "۱",
        "²",
        " ",
        "\n",
        "\r",
        "\x1c",
        "\u2003",
        "نام",
        "🌍",
        "'",
        '"',
        "(",
        "{",
        "[",
        "/",
        "<",
        "@0001**",
        "x>t",
        "#**x>t@1**",
    ]
    for _ in range(count):
        text = "".join(rng.choices(atoms, k=rng.randrange(30)))
        yield text, rng.randrange(len(text.encode()) + 2)


def syntax_expected(pattern: re.Pattern[str], text: str, start: int) -> str:
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
            match.group("stream_name").encode().hex(),
            match.group("topic_name").encode().hex() if "topic_name" in pattern.groupindex else "-",
            match.group("message_id").encode().hex() if "message_id" in pattern.groupindex else "-",
        ]
    )


def field_cases(seed: int, count: int) -> Iterator[tuple[str, int, str, str, int | None, str]]:
    names = ["", "dev tools", "<نام &>", "\n\t", "a.b/%~()_-", "İΣ🌍", "x\x02klzzwxh:0000\x03"]
    topics = ["", "a>b@old", "<topic>&", "a.b /%()", "نام🌍", "\n\t"]
    messages = ["0", "0001", "۱٢3", "9" * 5000]
    for kind, channel, topic, operand, message in itertools.product(
        KINDS, names, topics, [None, 0, 99], messages
    ):
        yield kind, 42, channel, topic, operand, message
    rng = random.Random(seed)
    atoms = [
        "a",
        " ",
        "\n",
        "\t",
        "<",
        "&",
        '"',
        "'",
        ".",
        "/",
        "%",
        "#",
        "نام",
        "🌍",
        ">",
        "@",
        "(",
        ")",
        "\x02",
        "\x03",
    ]
    for _ in range(count):
        yield (
            rng.choice(KINDS),
            rng.choice([0, 1, 42, 2147483647]),
            "".join(rng.choices(atoms, k=rng.randrange(40))),
            "".join(rng.choices(atoms, k=rng.randrange(40))),
            rng.choice([None, 0, 99, 2147483647]),
            rng.choice(messages),
        )


def field_expected(
    handler: Handler,
    kind: str,
    channel_id: int,
    channel: str,
    topic: str,
    operand: int | None,
    message: str,
) -> str:
    context = SimpleNamespace(
        find_stream_id=lambda name: channel_id,
        is_empty_topic_only_channel=lambda name: False,
        get_with_operand=lambda info: operand,
    )
    groups = {"stream_name": channel, "topic_name": topic, "message_id": message}
    match = SimpleNamespace(group=lambda name: groups[name], start=lambda: 0, end=lambda: 1)
    element, _, _ = handler(context, match, "")
    assert isinstance(element.text, AtomicString)
    assert len(element) == int(kind != "channel" and topic == "")
    tail = None
    if len(element):
        child = element[0]
        assert child.tag == "em" and child.text == "<empty & نام>" and not child.attrib
        tail = child.tail
        if tail is not None:
            assert isinstance(tail, AtomicString)
    return "\t".join(
        [
            "fields",
            str(len(element.attrib)),
            *(value.encode().hex() for pair in element.attrib.items() for value in pair),
            element.text.encode().hex(),
            str(int(bool(len(element)))),
            tail.encode().hex() if tail is not None else "-",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare native channel links with Python regexes and handlers"
    )
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--reference-only", action="store_true")
    parser.add_argument("--cases", type=int, default=100000)
    parser.add_argument("--seed", type=int, default=962830)
    parser.add_argument("--all-boundaries", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cases < 0 or (args.probe is None and not args.reference_only):
        parser.error("nonnegative --cases and --probe (or --reference-only) are required")
    root = Path(__file__).resolve().parents[2]
    regexes, handlers = reference(root)
    digits = "".join(chr(p) for p in range(128, 0x110000) if chr(p).isdecimal())
    checked = 0
    counts: dict[str, int] = {}
    mismatches = 0
    failures: list[dict[str, str]] = []
    digest = hashlib.sha256()

    def compare(inputs: list[str], wanted: list[str]) -> None:
        nonlocal checked, mismatches
        if args.reference_only:
            actual = wanted
        else:
            result = subprocess.run(
                [str(args.probe.resolve())],
                input=f"digits\t{digits.encode().hex()}\n" + "\n".join(inputs) + "\n",
                capture_output=True,
                text=True,
                check=True,
            )
            actual = result.stdout.splitlines()
            assert len(actual) == len(inputs), result.stderr
        for request, expected, observed in zip(inputs, wanted, actual, strict=True):
            checked += 1
            kind = observed.split("\t")[0]
            counts[kind] = counts.get(kind, 0) + 1
            digest.update(json.dumps([request, expected]).encode() + b"\n")
            if expected != observed:
                mismatches += 1
                if len(failures) < 10:
                    failures.append({"input": request, "expected": expected, "actual": observed})

    corpus = iter(syntax_cases(args.seed, args.cases, args.all_boundaries))
    while batch := list(itertools.islice(corpus, 2048)):
        compare(
            [f"{kind}\t{start}\t{text.encode().hex()}" for text, start in batch for kind in KINDS],
            [
                syntax_expected(regexes[kind], text, start)
                for text, start in batch
                for kind in KINDS
            ],
        )
    syntax_count = checked
    fields = iter(field_cases(args.seed, args.cases))
    while field_batch := list(itertools.islice(fields, 512)):
        compare(
            [
                "\t".join(
                    [
                        "fields",
                        str(channel_id),
                        channel.encode().hex(),
                        topic.encode().hex() if kind != "channel" else "-",
                        str(operand) if operand is not None else "-",
                        message.encode().hex() if kind == "message" else "-",
                    ]
                )
                for kind, channel_id, channel, topic, operand, message in field_batch
            ],
            [
                field_expected(handlers[kind], kind, channel_id, channel, topic, operand, message)
                for kind, channel_id, channel, topic, operand, message in field_batch
            ],
        )
    sources = [
        Path(__file__),
        *(
            root / path
            for path in [
                "zerver/lib/mention.py",
                "zerver/lib/markdown/__init__.py",
                "zerver/lib/url_encoding.py",
                "zerver/lib/topic_link_util.py",
                "rust-backend/src/markdown_channel_syntax.rs",
                "rust-backend/src/markdown_channel_spans.rs",
                "rust-backend/src/url_fragments.rs",
                "rust-backend/examples/channel_links.rs",
            ]
        ),
    ]
    if not args.reference_only:
        sources.append(args.probe)
    report = {
        "python": platform.python_version(),
        "seed": args.seed,
        "all_boundaries": args.all_boundaries,
        "reference_only": args.reference_only,
        "reference_sha256": digest.hexdigest(),
        "cases": checked,
        "syntax_cases": syntax_count,
        "field_cases": checked - syntax_count,
        "counts": counts,
        "mismatches": mismatches,
        "failures": failures,
        "sha256": {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ["cases", "counts", "mismatches"]}))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
