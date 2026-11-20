# ruff: noqa: INP001
import argparse
import hashlib
import json
import os
import platform
import random
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast
from xml.etree.ElementTree import Element

if TYPE_CHECKING:
    from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor

Text = tuple[str, bool] | None


@dataclass
class Node:
    tag: str
    text: Text
    tail: Text
    children: list[int]


@dataclass
class Case:
    nodes: list[Node]
    ancestors: list[str] | None
    registry: str = "default"
    accepted: bool | None = None


def build(case: Case) -> Element:
    from markdown.util import AtomicString

    def text(value: Text) -> str | None:
        if value is None:
            return None
        return AtomicString(value[0]) if value[1] else value[0]

    nodes = [Element(spec.tag, {"source": str(index)}) for index, spec in enumerate(case.nodes)]
    for node, spec in zip(nodes, case.nodes, strict=True):
        node.text = text(spec.text)
        node.tail = text(spec.tail)
        node[:] = [nodes[index] for index in spec.children]
    return nodes[0]


def snapshot(processor: "PythonInlineProcessor", root: Element) -> dict[str, object]:
    from zerver.lib.markdown.native_inline import RegistryInternals

    nodes = [root]
    indices = {root: 0}

    def index(node: Element) -> int:
        if node not in indices:
            indices[node] = len(nodes)
            nodes.append(node)
        return indices[node]

    def text(value: str | None) -> tuple[str | None, str]:
        return value, type(value).__name__

    stash = [
        (key, text(value) if isinstance(value, str) else index(value))
        for key, value in processor.stashed_nodes.items()
    ]
    parents = [(index(node), index(parent)) for node, parent in processor.parent_map.items()]
    tree = [
        (node.tag, text(node.text), text(node.tail), dict(node.attrib), [index(c) for c in node])
        for node in nodes
    ]
    registry = cast(RegistryInternals, processor.inlinePatterns)
    return {
        "tree": tree,
        "stash": stash,
        "parents": parents,
        "ancestors": processor.ancestors,
        "registry": (list(registry._priority), registry._is_sorted),
        "html_stash": (
            processor.md.htmlStash.html_counter,
            processor.md.htmlStash.rawHtmlBlocks[:],
        ),
    }


def exercise(case: Case, *, native: bool) -> tuple[bool, dict[str, object]]:
    from markdown import util
    from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor

    from zerver.lib.markdown import ZulipMarkdown, native_inline
    from zerver.lib.markdown.inline_stash import InlineProcessor

    md = ZulipMarkdown(linkifiers=[], linkifiers_key=0, email_gateway=False)
    md.zulip_db_data = None
    registry = cast(native_inline.RegistryInternals, md.inlinePatterns)
    if case.registry == "empty":
        md.inlinePatterns = util.Registry()
    elif case.registry == "formatting":
        for name in list(registry._data):
            if name not in {
                "backtick",
                "strong_em",
                "strong",
                "emphasis",
                "del",
                "not_strong",
                "nl",
            }:
                md.inlinePatterns.deregister(name)
    elif case.registry == "excluded":
        for name in ("backtick", "strong", "emphasis"):
            md.inlinePatterns[name].__dict__["ANCESTOR_EXCLUDES"] = ("P", "outer")
    processor = InlineProcessor(md) if native else PythonInlineProcessor(md)
    md.treeprocessors.register(processor, "inline", 25)
    root = build(case)
    ancestors = None if case.ancestors is None else case.ancestors[:]
    accepted = False
    if native:
        original_nodes = list(dict.fromkeys(root.iter()))
        original_values = [
            (node.tag, node.text, node.tail, dict(node.attrib), list(node))
            for node in original_nodes
        ]
        original_attributes = [node.attrib for node in original_nodes]
        original_registry = cast(native_inline.RegistryInternals, md.inlinePatterns)
        priorities = original_registry._priority[:]
        sorted_before = original_registry._is_sorted
        accepted = native_inline.try_run(
            cast(InlineProcessor, processor),
            root,
            ancestors,
            import_module("_zulip_backend").plan_markdown_native_inline,
        )
        assert set(original_nodes).issubset(root.iter()), "Plan replaced an original element"
        assert all(
            node.attrib is attrs
            for node, attrs in zip(original_nodes, original_attributes, strict=True)
        )
        if not accepted:
            assert original_values == [
                (node.tag, node.text, node.tail, dict(node.attrib), list(node))
                for node in original_nodes
            ], "Rejected plan mutated the input graph"
            assert (priorities, sorted_before) == (
                original_registry._priority,
                original_registry._is_sorted,
            ), "Rejected plan mutated the registry"
            assert not hasattr(processor, "stashed_nodes"), "Rejected plan created processor state"
    error = None
    if not accepted:
        try:
            PythonInlineProcessor.run(processor, root, ancestors)
        except (RecursionError, ValueError, TypeError, IndexError, KeyError) as exc:
            error = (type(exc).__name__, str(exc))
    state = snapshot(processor, root)
    state["error"] = error
    state["input_ancestors"] = ancestors
    return accepted, state


def cases(seed: int, count: int) -> Iterator[Case]:
    for registry in ("default", "formatting", "excluded", "empty"):
        for initial_text in (
            None,
            ("", False),
            ("plain فارسی", False),
            ("**bold**", False),
            ("**atomic**", True),
        ):
            yield Case(
                [Node("div", None, None, [1]), Node("p", initial_text, None, [])],
                None,
                registry,
                accepted=True,
            )
    for unsupported in (
        ":نام:",
        "😀",
        "$$x$$",
        "&copy;",
        "[link](https://example.com)",
        "\ud800",
    ):
        yield Case(
            [
                Node("div", None, None, [1]),
                Node("p", (f"**earlier** {unsupported}", False), None, []),
            ],
            None,
            accepted=False,
        )
    for value in (":smile:", ":zulip:", ":unknown:", ":+1:", "***:smile:*** `:zulip:`"):
        yield Case(
            [Node("div", None, None, [1]), Node("p", (value, False), None, [])],
            None,
            accepted=True,
        )
    rng = random.Random(seed)
    pieces = [
        "***",
        "**",
        "*",
        "~~",
        "`",
        "\\",
        "فارسی",
        "abc",
        "123",
        "\n",
        " ",
        "_",
        "^",
        "<&>",
        "\x02klzzwxh:bad",
        "\x02klzzwxh:9999\x03",
        "&copy;",
        ":smile:",
        ":zulip:",
        ":unknown:",
        ":+1:",
        ":نام:",
        "😀",
        "$$x$$",
        "[x](https://example.com)",
        "\ud800",
    ]
    for _ in range(count):
        nodes = []
        for index in range(rng.randrange(1, 9)):
            values: list[Text] = []
            for _ in range(2):
                value = "".join(rng.choices(pieces, k=rng.randrange(10)))
                values.append(None if rng.randrange(5) == 0 else (value, rng.randrange(8) == 0))
            nodes.append(
                Node(
                    rng.choice(["div", "p", "P", "span", "code", "a", "strong", "İ", ""]),
                    values[0],
                    values[1],
                    [],
                )
            )
            if index:
                nodes[rng.randrange(index)].children.append(index)
                if rng.randrange(5) == 0:
                    nodes[rng.randrange(index)].children.append(index)
        yield Case(
            nodes,
            rng.choice([None, [], ["outer"], ["a", "P"]]),
            rng.choice(["default", "default", "formatting", "excluded", "empty"]),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare native inline graph state with Python-Markdown."
    )
    parser.add_argument("--cases", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=962817)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-failures", type=int, default=10)
    args = parser.parse_args()
    if args.cases < 0 or args.max_failures < 1:
        parser.error("cases must be nonnegative and max-failures must be positive")
    if any(name.startswith("ZULIP_RUST_") and value == "1" for name, value in os.environ.items()):
        parser.error("unset Rust feature flags to isolate native inline execution")
    sys.path.insert(0, str(args.repository.resolve()))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")
    import django

    django.setup()
    native = import_module("_zulip_backend")
    if not hasattr(native, "plan_markdown_native_inline"):
        parser.error("the loaded Rust extension does not contain the native inline planner")
    extension = (
        import_module(f"{native.__name__}._zulip_backend")
        if hasattr(native, "__path__")
        else native
    )
    extension_path = extension.__file__
    if extension_path is None:
        parser.error("the loaded extension has no file path")
    files = [
        Path(native.__file__ or extension_path),
        Path(extension_path),
        Path(__file__),
        args.repository / "rust-backend/src/markdown_native_inline.rs",
        args.repository / "zerver/lib/markdown/native_inline.py",
    ]
    report: dict[str, object] = {
        "python": platform.python_version(),
        "extension": extension_path,
        "seed": args.seed,
        "generated_cases": args.cases,
        "hashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
    }
    failures = []
    accepted = delegated = checked = 0
    for number, case in enumerate(cases(args.seed, args.cases)):
        if number % 100 == 0:
            print(f"Checking case {number}", flush=True)
        try:
            _, expected = exercise(case, native=False)
            used_native, actual = exercise(case, native=True)
        except Exception as exc:
            failures.append(
                {
                    "case": number,
                    "input": asdict(case),
                    "error": (type(exc).__name__, str(exc)),
                }
            )
            print(f"Case {number} raised {type(exc).__name__}: {exc}", flush=True)
            break
        accepted += used_native
        delegated += not used_native
        checked += 1
        fields = [field for field in expected if expected[field] != actual[field]]
        if case.accepted is not None and used_native != case.accepted:
            fields.append("native_acceptance")
        if fields:
            failures.append(
                {
                    "case": number,
                    "input": asdict(case),
                    "native": used_native,
                    "expected_native": case.accepted,
                    "fields": fields,
                    "expected": expected,
                    "actual": actual,
                }
            )
            print(f"Mismatch at case {number}: {', '.join(fields)}", flush=True)
            if len(failures) >= args.max_failures:
                break
    report.update(checked=checked, accepted=accepted, delegated=delegated, failures=failures)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    print(
        f"{checked} checked; {accepted} native; {delegated} delegated; {len(failures)} mismatches",
        flush=True,
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
