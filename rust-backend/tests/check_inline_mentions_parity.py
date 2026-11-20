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
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from xml.etree.ElementTree import Element


@dataclass
class User:
    id: int
    full_name: str
    is_active: bool


class AtomicString(str):
    __slots__ = ()


Case = tuple[str, str, bool, int, list[User], int | None, bool, bool]
Handler = Callable[[object, object, str], tuple[Element | None, int | None, int | None]]


def handlers(source: Path, mention_source: Path) -> dict[str, Handler]:
    module = ast.parse(source.read_text())
    functions: list[ast.stmt] = []
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name in {
            "UserMentionPattern",
            "UserGroupMentionPattern",
        }:
            method = next(
                method
                for method in node.body
                if isinstance(method, ast.FunctionDef) and method.name == "handleMatch"
            )
            method.name = "user" if node.name == "UserMentionPattern" else "group"
            method.decorator_list = []
            functions.append(method)
    assert len(functions) == 2
    mention_nodes: list[ast.stmt] = []
    for node in ast.parse(mention_source.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "user_mention_matches_topic_wildcard",
            "user_mention_matches_stream_wildcard",
        }:
            mention_nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in {"topic_wildcards", "stream_wildcards"}
            for t in node.targets
        ):
            mention_nodes.append(node)
    assert len(mention_nodes) == 4
    scope: dict[str, object] = {}
    # Only execute the selected definitions from the trusted reference checkout.
    exec(  # noqa: S102
        compile(
            ast.Module(body=mention_nodes, type_ignores=[]),
            str(mention_source),
            "exec",
            flags=__future__.annotations.compiler_flag,
        ),
        scope,
    )
    scope.update(
        {
            "mention": SimpleNamespace(**scope),
            "re": re,
            "Element": Element,
            "markdown": SimpleNamespace(util=SimpleNamespace(AtomicString=AtomicString)),
            "FullNameInfo": User,
            "_rust_markdown_inline_mentions": None,
            "get_user_group_mention_display_name": lambda group: group.display_name,
        }
    )
    exec(  # noqa: S102
        compile(
            ast.Module(body=functions, type_ignores=[]),
            str(source),
            "exec",
            flags=__future__.annotations.compiler_flag,
        ),
        scope,
    )
    return {name: cast(Handler, scope[name]) for name in ["user", "group"]}


def cases(seed: int, count: int) -> Iterator[Case]:
    names = [
        "Alice",
        "ALICE",
        "alice|42",
        "Alice|42",
        "|42",
        "|٤۲",
        "|00042",
        "|42\n",
        "Alice|42\n",
        "Alice\n|42",
        "|²",
        "|+42",
        "|-42",
        "|42 ",
        "|42\n\n",
        "a|b|42",
        "all",
        "everyone",
        "stream",
        "channel",
        "topic",
        "All",
        "Topic",
        "",
        "<Group & 📝>",
        "نام",
        "İΣ",
        "\x02klzzwxh:0000\x03",
        "|2147483647",
        "|2147483648",
        "|0",
        "|" + "0" * 640 + "42",
        "|" + "0" * 4300 + "42",
        "|" + "9" * 4301,
    ]
    for name, silent, active, found, kind in itertools.product(
        names, [False, True], [False, True], [False, True], ["user", "group"]
    ):
        users = [User(42, "Alice", active), User(17, "نام", True)]
        yield (
            kind,
            name,
            silent,
            4300,
            users if found else [],
            0 if found else None,
            not active,
            found,
        )
    for point in range(0x110000):
        char = chr(point)
        if char.isdecimal() and not char.isascii():
            yield "user", f"|{char}", False, 4300, [User(int(char), "رقم", True)], None, False, True
    rng = random.Random(seed)
    atoms = [
        "a",
        "A",
        "|",
        "0",
        "2",
        "4",
        "9",
        "٤",
        "۲",
        "𝟜",
        "¹",
        "\n",
        "\r",
        "\t",
        " ",
        "نام",
        "Σ",
        "İ",
        "<>&",
        "@",
        "_",
        "topic",
        "all",
        "\x02klzzwxh:0000\x03",
    ]
    for _ in range(count):
        users = [
            User(
                42,
                rng.choice(["Alice", "نام", "a|b", "<>&_[]", "Σ", "\x02klzzwxh:0000\x03"]),
                bool(rng.randrange(2)),
            ),
            User(17, "نام", True),
        ]
        name = (
            rng.choice(names)
            if rng.randrange(2)
            else "".join(rng.choices(atoms, k=rng.randrange(15)))
        )
        if rng.randrange(3) == 0:
            name = (
                rng.choice(["", users[0].full_name, users[0].full_name.swapcase()])
                + "|"
                + rng.choice(["42", "٤۲", "00042", "42\n", "17", "999999999999999999999"])
            )
        found = bool(rng.randrange(2))
        yield (
            rng.choice(["user", "group"]),
            name,
            bool(rng.randrange(2)),
            rng.choice([0, 640, 4300]),
            users if found else [],
            rng.choice([None, 0, 1]) if found else None,
            bool(rng.randrange(2)),
            found,
        )


def encode(case: Case) -> str:
    kind, name, silent, limit, users, by_name, deactivated, found = case
    if kind == "group":
        fields = [
            kind,
            name.encode().hex(),
            str(int(silent)),
            str(int(deactivated)),
            "15",
            str(int(found)),
        ]
    else:
        fields = [
            kind,
            name.encode().hex(),
            str(int(silent)),
            str(limit),
            str(len(users)),
            "-" if by_name is None else str(by_name),
        ]
        for user in users:
            fields.extend([str(user.id), user.full_name.encode().hex(), str(int(user.is_active))])
    return "\t".join(fields) + "\n"


def expected(case: Case, callbacks: dict[str, Handler]) -> str:
    kind, name, silent, limit, users, by_name, deactivated, found = case
    group = SimpleNamespace(id=15, display_name=name, deactivated=deactivated)
    data = SimpleNamespace(
        get_user_by_id=lambda id: next((user for user in reversed(users) if user.id == id), None),
        get_user_by_name=lambda name: None if by_name is None else users[by_name],
        get_user_group=lambda name: group if found else None,
    )
    effects = SimpleNamespace(
        mentions_user_ids=set(),
        mentions_user_group_ids=set(),
        mentions_stream_wildcard=False,
        mentions_topic_wildcard=False,
    )
    processor = SimpleNamespace(
        zmd=SimpleNamespace(
            zulip_db_data=SimpleNamespace(mention_data=data), zulip_rendering_result=effects
        )
    )
    match = SimpleNamespace(
        group=lambda key: name if key == "match" else "_" if silent else "",
        start=lambda: 0,
        end=lambda: len(name),
    )
    old_limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(limit)
    try:
        try:
            element, _, _ = callbacks[kind](processor, match, "")
        except ValueError:
            return "delegate"
    finally:
        sys.set_int_max_str_digits(old_limit)
    if element is None:
        return "none"
    assert isinstance(element.text, AtomicString)
    notification = "-"
    identifier = "-"
    if effects.mentions_user_ids:
        notification, identifier = "user", str(next(iter(effects.mentions_user_ids)))
    elif effects.mentions_user_group_ids:
        notification, identifier = "group", str(next(iter(effects.mentions_user_group_ids)))
    elif effects.mentions_stream_wildcard:
        notification = "stream"
    elif effects.mentions_topic_wildcard:
        notification = "topic"
    fields = ["span", element.text.encode().hex(), notification, identifier]
    for key, value in element.items():
        fields.extend([key.encode().hex(), value.encode().hex()])
    return "\t".join(fields)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare native mention fields with reference handlers"
    )
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--reference-only", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--mention-source", type=Path)
    parser.add_argument("--cases", type=int, default=100000)
    parser.add_argument("--seed", type=int, default=962828)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cases < 0 or (args.probe is None and not args.reference_only):
        parser.error("require nonnegative --cases and --probe unless --reference-only")
    root = Path(__file__).resolve().parents[2]
    source = args.source or root / "zerver/lib/markdown/__init__.py"
    mention_source = args.mention_source or root / "zerver/lib/mention.py"
    callbacks = handlers(source, mention_source)
    decimal = [
        chr(point)
        for point in range(0x110000)
        if chr(point).isdecimal() and not chr(point).isascii()
    ]
    header = (
        "digits\t"
        + "".join(decimal).encode().hex()
        + "\t"
        + "".join(str(int(char)) for char in decimal)
        + "\n"
    )
    corpus = iter(cases(args.seed, args.cases))
    counts = {"span": 0, "none": 0, "delegate": 0}
    failures: list[dict[str, object]] = []
    mismatches = checked = 0
    digest = hashlib.sha256()
    while batch := list(itertools.islice(corpus, 512)):
        wanted = [expected(case, callbacks) for case in batch]
        if args.reference_only:
            actual = wanted
        else:
            process = subprocess.run(
                [str(args.probe.resolve())],
                input=header + "".join(map(encode, batch)),
                text=True,
                capture_output=True,
                check=True,
            )
            actual = process.stdout.splitlines()
            assert len(actual) == len(batch), process.stderr
        for case, target, output in zip(batch, wanted, actual, strict=True):
            digest.update((encode(case) + target + "\n").encode())
            checked += 1
            counts[output.split("\t")[0]] += 1
            if target != output:
                mismatches += 1
                if len(failures) < 10:
                    failures.append({"case": encode(case), "expected": target, "actual": output})
    sources = [Path(__file__), source, mention_source]
    if not args.reference_only:
        sources.extend(
            [
                root / "rust-backend/src/markdown_inline_mentions.rs",
                root / "rust-backend/examples/inline_mentions.rs",
                args.probe,
            ]
        )
    report = {
        "python": platform.python_version(),
        "seed": args.seed,
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
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
