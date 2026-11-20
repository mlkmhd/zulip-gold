# ruff: noqa: INP001
import __future__

import argparse
import ast
import copy
import hashlib
import inspect
import itertools
import json
import os
import platform
import random
import re
import runpy
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from xml.etree.ElementTree import Element, SubElement

import markdown
from check_inline_mentions_parity import Handler, User, handlers
from check_media_plan_parity import decode_plan, snapshot
from check_mention_syntax_parity import patterns
from markdown import inlinepatterns, util
from markdown.treeprocessors import InlineProcessor
from typing_extensions import override


class MentionPattern(inlinepatterns.InlineProcessor):
    def __init__(self, pattern: re.Pattern[str], md: markdown.Markdown, handler: Handler) -> None:
        super().__init__(pattern.pattern, md)
        self.zmd = md
        self.handler = handler

    @override
    def handleMatch(  # type: ignore[override] # Matches the selected reference handlers.
        self, match: re.Match[str], data: str
    ) -> tuple[Element | None, int | None, int | None]:
        return self.handler(self, match, data)


class ExcludedMentionPattern(MentionPattern):
    ANCESTOR_EXCLUDES = ["code"]


def reference_definitions(
    root: Path,
) -> tuple[type[inlinepatterns.InlineProcessor], type, dict[str, str]]:
    source = root / "zerver/lib/markdown/__init__.py"
    nodes = ast.parse(source.read_text()).body
    selected: list[ast.stmt] = [
        node
        for node in nodes
        if isinstance(node, ast.ClassDef)
        and node.name in {"BacktickInlineProcessor", "MessageRenderingResult"}
    ]
    assert len(selected) == 2
    scope: dict[str, object] = {
        "markdown": markdown,
        "Element": Element,
        "dataclass": dataclass,
        "override": lambda function: function,
        **runpy.run_path(str(root / "zerver/lib/markdown/inline_code.py")),
    }
    scope["__name__"] = __name__
    exec(  # noqa: S102 # Only selected definitions from the trusted reference checkout.
        compile(
            ast.Module(body=selected, type_ignores=[]),
            str(source),
            "exec",
            flags=__future__.annotations.compiler_flag,
        ),
        scope,
    )
    build = next(
        method
        for node in nodes
        if isinstance(node, ast.ClassDef) and node.name == "ZulipMarkdown"
        for method in node.body
        if isinstance(method, ast.FunctionDef) and method.name == "build_inlinepatterns"
    )
    expressions = {
        target.id: ast.literal_eval(node.value)
        for node in build.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {"STRONG_EM_RE", "STRONG_RE", "EMPHASIS_RE"}
    }
    assert len(expressions) == 3
    return (
        cast(type[inlinepatterns.InlineProcessor], scope["BacktickInlineProcessor"]),
        cast(type, scope["MessageRenderingResult"]),
        expressions,
    )


def cases(seed: int, count: int) -> Iterator[str]:
    yield ""
    yield "plain فارسی"
    names = [
        "Alice",
        "alice",
        "ALICE",
        "|42",
        "Alice|42",
        "Wrong|42",
        "|۰۰4۲",
        "|42\n",
        "|42\n\n",
        "|²",
        "|+42",
        "|2147483648",
        "all",
        "All",
        "everyone",
        "stream",
        "channel",
        "topic",
        "نام",
        "|17",
        "İΣ",
        "unknown",
    ]
    for name, silent in itertools.product(names, ["", "_"]):
        yield f"@{silent}**{name}** @*group*"
        yield f"@{silent}*{name}* @**Alice**"
    yield from [
        "@**unknown** @**Wrong|42** @**|42** @**Alice**",
        "@**Alice** @_**Alice** @*group* @_*group* @*old* @**topic** @_**topic**",
        "@**all** @**everyone** @**channel** @**stream** @**topic**",
        "@**نام** @*نام* @**|17** @**|7**",
        "` @**Alice** ` @**Alice**",
        "***@**Alice** *** **@*group* **",
        "@**open @**Alice** @**topic**",
        "@*open @*group* @**topic**",
        "@**Alice\n|42** @**Alice|42\n**",
        "x@**Alice** (@**Alice**) [@*group*] <@**topic**",
        "@**Alice** @**unknown** @*group*",
        "\x02klzzwxh:bad\x03 @**topic**",
        "@**|" + "0" * 641 + "42** @**Alice**",
        "@**|" + "0" * 4301 + "42** @**Alice**",
    ]
    for point in range(0x110000):
        if chr(point).isdecimal():
            yield f"@**|00{chr(point)}** @**Alice**"
    atoms = [
        "@**Alice**",
        "@**alice**",
        "@**ALICE**",
        "@**|٤۲**",
        "@**Wrong|42**",
        "@_**Alice**",
        "@*group*",
        "@_*group*",
        "@*old*",
        "@**topic**",
        "@**All**",
        "@**all**",
        "@**everyone**",
        "@**نام**",
        "@*نام*",
        "@**İΣ**",
        "@*GROUP*",
        "@**unknown**",
        "@*unknown*",
        "@",
        "*",
        "**",
        "***",
        "**bold**",
        "*em*",
        "_",
        "|",
        "42",
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
        "]",
        "`",
        "\\",
        "\x02klzzwxh:bad\x03",
    ]
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choices(atoms, k=rng.randrange(30)))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare mention arena graphs and pending notifications"
    )
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=962832)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    assert not any(
        key.startswith("ZULIP_RUST_") and value == "1" for key, value in os.environ.items()
    )
    root = Path(__file__).resolve().parents[2]
    handler_map = handlers(root / "zerver/lib/markdown/__init__.py", root / "zerver/lib/mention.py")
    for handler in handler_map.values():
        handler.__globals__["markdown"] = markdown
    regexes = patterns(root / "zerver/lib/mention.py")
    backtick, rendering_class, expressions = reference_definitions(root)
    corpus = list(cases(args.seed, args.cases))
    digits = [chr(p) for p in range(128, 0x110000) if chr(p).isdecimal()]
    decimal_header = (
        "digits\t"
        + "".join(digits).encode().hex()
        + "\t"
        + "".join(str(int(c)) for c in digits)
        + "\n"
    )
    failures: list[dict[str, object]] = []
    counts = dict.fromkeys(
        ["accepted", "delegated", "user", "group", "channel", "topic", "silent"], 0
    )
    checked = 0
    original_limit = sys.get_int_max_str_digits()
    try:
        for context, registry, atomic, excluded, limit in itertools.product(
            range(4), ["normal", "reverse"], [False, True], [False, True], [640, 4300]
        ):
            sys.set_int_max_str_digits(limit)
            for offset in range(0, len(corpus), 1024):
                batch = corpus[offset : offset + 1024]
                result = subprocess.run(
                    [str(args.probe.resolve())],
                    input=decimal_header
                    + "".join(
                        f"plan\t{text.encode().hex()}\t{context}\t{registry}\t{int(atomic)}\t{int(excluded)}\t{limit}\t"
                        + ",".join(
                            match.group("match").encode().hex()
                            for match in regexes["user"].finditer(text)
                        )
                        + "\t"
                        + ",".join(
                            match.group("match").encode().hex()
                            for match in regexes["group"].finditer(text)
                        )
                        + "\n"
                        for text in batch
                    ),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                lines = result.stdout.splitlines()
                assert len(lines) == len(batch), result.stderr
                for text, line in zip(batch, lines, strict=True):
                    checked += 1
                    if line == "delegate":
                        counts["delegated"] += 1
                        limited_id = (
                            context != 0 and not excluded and limit == 640 and "0" * 641 in text
                        )
                        if atomic or (
                            text
                            in {
                                "",
                                "plain فارسی",
                                "@**all** @**everyone** @**channel** @**stream** @**topic**",
                            }
                            and not limited_id
                        ):
                            failures.append(
                                {
                                    "text": text,
                                    "error": "required native acceptance",
                                    "context": context,
                                    "atomic": atomic,
                                    "excluded": excluded,
                                    "limit": limit,
                                }
                            )
                        continue
                    counts["accepted"] += 1
                    md = markdown.Markdown()
                    isolated = cast(SimpleNamespace, md)
                    users = (
                        {
                            42: User(42, "Alice", context != 3),
                            17: User(17, "نام <&**Bold**>", context != 3),
                            7: User(7, "İΣ", context != 3),
                        }
                        if context > 1
                        else {}
                    )
                    names = (
                        {
                            name: users[id]
                            for name, id in [
                                ("Alice", 42),
                                ("alice", 42),
                                ("ALICE", 42),
                                ("نام", 17),
                                ("İΣ", 7),
                            ]
                        }
                        if users
                        else {}
                    )
                    groups = (
                        {
                            name: SimpleNamespace(
                                id=id, display_name=display, deactivated=deactivated or context == 3
                            )
                            for name, id, display, deactivated in [
                                ("group", 15, "Group <&**Bold**>", False),
                                ("GROUP", 15, "Group <&**Bold**>", False),
                                ("old", 99, "Old", True),
                                ("نام", 17, "گروه", False),
                            ]
                        }
                        if context > 1
                        else {}
                    )
                    isolated.zulip_db_data = (
                        None
                        if context == 0
                        else SimpleNamespace(
                            mention_data=SimpleNamespace(
                                get_user_by_name=names.get,
                                get_user_by_id=users.get,
                                get_user_group=groups.get,
                            )
                        )
                    )
                    pattern_class = ExcludedMentionPattern if excluded else MentionPattern
                    user = pattern_class(regexes["user"], md, handler_map["user"])
                    group = pattern_class(regexes["group"], md, handler_map["group"])
                    processors = {
                        "user": user,
                        "group": group,
                        "code": backtick(inlinepatterns.BACKTICK_RE),
                        "strong_em": inlinepatterns.DoubleTagPattern(
                            expressions["STRONG_EM_RE"], "strong,em"
                        ),
                        "strong": inlinepatterns.SimpleTagPattern(
                            expressions["STRONG_RE"], "strong"
                        ),
                        "em": inlinepatterns.SimpleTagPattern(expressions["EMPHASIS_RE"], "em"),
                        "nl": inlinepatterns.SubstituteTagInlineProcessor(r"\n", "br"),
                    }
                    order = (
                        ["code", "strong_em", "user", "group", "strong", "em", "nl"]
                        if registry == "normal"
                        else ["group", "user", "code", "strong_em", "strong", "em", "nl"]
                    )
                    md.inlinePatterns = util.Registry()
                    for priority, name in enumerate(order):
                        md.inlinePatterns.register(processors[name], name, 100 - priority)
                    rendering = rendering_class(
                        "sentinel",
                        checked % 3 == 0,
                        checked % 5 == 0,
                        {1},
                        {2},
                        {"word"},
                        {"url"},
                        {3},
                        ["a", "a"],
                        {"spinner"},
                        True,
                        True,
                    )
                    isolated.zulip_rendering_result = rendering
                    actual_rendering = copy.deepcopy(rendering)
                    processor = InlineProcessor(md)
                    md.treeprocessors.register(processor, "inline", 25)
                    tree = Element("div")
                    SubElement(tree, "p").text = util.AtomicString(text) if atomic else text
                    try:
                        processor.run(tree, ["code" if excluded else "outer"])
                    except ValueError as error:
                        failures.append({"text": text, "error": repr(error), "native": line})
                        continue
                    expected = snapshot(
                        tree, processor.stashed_nodes, processor.parent_map, processor.ancestors
                    )
                    graph, effects = line.split("\tnotifications\t")
                    actual, paths, has_image = decode_plan(graph)
                    user_ids, group_ids, channel, topic = effects.split("\t")
                    actual_rendering.mentions_user_ids.update(
                        int(id) for id in user_ids.split(",") if id
                    )
                    actual_rendering.mentions_user_group_ids.update(
                        int(id) for id in group_ids.split(",") if id
                    )
                    actual_rendering.mentions_stream_wildcard |= channel == "1"
                    actual_rendering.mentions_topic_wildcard |= topic == "1"
                    for key, value in [
                        ("user", user_ids),
                        ("group", group_ids),
                        ("channel", channel == "1"),
                        ("topic", topic == "1"),
                    ]:
                        counts[key] += bool(value)
                    counts["silent"] += any(
                        "silent" in node.get("class", "") for node in tree.iter("span")
                    )
                    if (
                        actual != expected
                        or paths
                        or has_image
                        or asdict(actual_rendering) != asdict(rendering)
                    ):
                        failures.append(
                            {
                                "text": text,
                                "context": context,
                                "registry": registry,
                                "atomic": atomic,
                                "excluded": excluded,
                                "limit": limit,
                                "expected": repr((expected, rendering)),
                                "actual": repr((actual, actual_rendering)),
                            }
                        )
                if len(failures) > 100:
                    break
            print(json.dumps({"progress": checked, "failures": len(failures)}), flush=True)
            if len(failures) > 100:
                break
    finally:
        sys.set_int_max_str_digits(original_limit)
    sources = [
        Path(__file__),
        args.probe,
        *(
            root / name
            for name in [
                "rust-backend/examples/mention_plan.rs",
                "rust-backend/src/markdown_native_inline.rs",
                "rust-backend/src/markdown_inline_mentions.rs",
                "rust-backend/src/markdown_mention_syntax.rs",
                "rust-backend/tests/check_inline_mentions_parity.py",
                "rust-backend/tests/check_mention_syntax_parity.py",
                "rust-backend/tests/check_media_plan_parity.py",
                "zerver/lib/markdown/__init__.py",
                "zerver/lib/markdown/inline_code.py",
                "zerver/lib/mention.py",
            ]
        ),
        Path(inspect.getfile(InlineProcessor)),
        Path(inspect.getfile(inlinepatterns)),
    ]
    report = {
        "python": platform.python_version(),
        "markdown_version": markdown.__version__,
        "seed": args.seed,
        "random_cases": args.cases,
        "checked": checked,
        "counts": counts,
        "failures": failures,
        "sha256": {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"sha256", "failures"}}
        )
    )
    assert not failures, f"{len(failures)} mismatches; see {args.report}"
    assert counts["accepted"] > checked // 2, "Insufficient native acceptance"
    assert all(counts[key] for key in ["user", "group", "channel", "topic", "silent"])


if __name__ == "__main__":
    main()
