# ruff: noqa: INP001
import argparse
import copy
import hashlib
import itertools
import json
import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch
from xml.etree.ElementTree import Element, SubElement

from check_media_plan_parity import NativeResult, snapshot
from check_tex_preflight_parity import cases
from markdown import util
from markdown.treeprocessors import InlineProcessor

if TYPE_CHECKING:
    from zerver.lib.markdown import ZulipMarkdown


def decode(line: str, registry_used: bool) -> tuple[NativeResult, list[str]]:
    graph, extra = line.split("\ttex\t")
    _, blocks = extra.split("\t")
    fields = graph.split("\t")

    def text(value: str) -> tuple[str, bool] | None:
        return None if value == "-" else (bytes.fromhex(value[1:]).decode(), value[0] == "1")

    nodes = []
    for field in fields[6:]:
        tag, lower, content, tail, children, attributes = field.split(",")
        attrs = [
            tuple(bytes.fromhex(part).decode() for part in pair.split(":"))
            for pair in attributes.split(";")
            if pair
        ]
        nodes.append(
            (
                bytes.fromhex(tag).decode(),
                bytes.fromhex(lower).decode(),
                text(content),
                text(tail),
                [int(value) for value in children.split(":") if value],
                cast(list[tuple[str, str]], attrs),
            )
        )
    parents = [tuple(map(int, pair.split(":"))) for pair in fields[4].split(",") if pair]
    stash: list[tuple[int | None, tuple[str, bool] | None]] = [
        (int(value[1:]), None) if value[0] == "n" else (None, text(value[1:]))
        for value in fields[5].split(",")
        if value
    ]
    ancestors = [bytes.fromhex(value).decode() for value in fields[3].split(",") if value]
    return (
        (
            nodes,
            cast(list[tuple[int, int]], parents),
            stash,
            ancestors,
            registry_used,
            ([], [], False, False),
        ),
        [bytes.fromhex(value[1:]).decode() for value in blocks.split(",") if value],
    )


def tree(data: str, shape: int) -> list[Element]:
    root = Element("div", {"kept": "root"})
    paragraph = SubElement(root, "p", {"kept": "paragraph"})
    paragraph.text = data
    nodes = [root, paragraph]
    if shape == 1:
        paragraph.text = "$$leading$$ &amp;"
        paragraph.tail = data
        last = SubElement(root, "p")
        last.text = "$$trailing$$"
        nodes.append(last)
    elif shape == 2:
        child = SubElement(paragraph, "span")
        child.text = "$$child$$"
        child.tail = "$$tail$$"
        nodes.append(child)
    elif shape == 3:
        paragraph.text = util.AtomicString(data)
    elif shape == 4:
        child = SubElement(paragraph, "span")
        child.text = "$$shared$$"
        child.tail = "$$shared-tail$$"
        other = SubElement(root, "p")
        other.text = "$$other$$"
        other.append(child)
        nodes.extend([child, other])
    return nodes


def capture(root: Element, processor: InlineProcessor, md: "ZulipMarkdown") -> object:
    return copy.deepcopy(
        (
            snapshot(root, processor.stashed_nodes, processor.parent_map, processor.ancestors),
            md.htmlStash.rawHtmlBlocks,
            md.htmlStash.html_counter,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply every certified TeX prefix to live Python objects"
    )
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--cases", default=200, type=int)
    parser.add_argument("--seed", default=962858, type=int)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--mutant-duplicate-html", action="store_true")
    args = parser.parse_args()
    assert args.cases >= 0
    assert not any(
        key.startswith("ZULIP_RUST_") and value == "1" for key, value in os.environ.items()
    )
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")
    import django

    django.setup()
    from django.conf import settings

    from zerver.lib import markdown as reference
    from zerver.lib.markdown.native_inline import RegistryInternals

    extension = import_module("_zulip_backend")
    constructor = extension.MarkdownInlineCheckpoint
    settings.ENABLE_FILE_LINKS = False
    orders = {
        "normal": [
            "backtick",
            "strong_em",
            "tex",
            "link",
            "entity",
            "strong",
            "emphasis",
            "del",
            "not_strong",
            "nl",
        ],
        "reverse": [
            "entity",
            "link",
            "tex",
            "backtick",
            "strong_em",
            "strong",
            "emphasis",
            "del",
            "not_strong",
            "nl",
        ],
        "last": [
            "backtick",
            "strong_em",
            "link",
            "entity",
            "strong",
            "emphasis",
            "del",
            "not_strong",
            "nl",
            "tex",
        ],
    }
    checked = delegated = states = calls = 0
    differences: list[dict[str, object]] = []
    source = list(cases(args.seed, args.cases))
    for registry, shape, mask in itertools.product(orders, range(5), [0, 63, 21, 42]):
        for offset in range(0, len(source), 64):
            batch = source[offset : offset + 64]
            counter = [0, 7, 9999, 1_000_000][(offset // 64) % 4]
            completed = subprocess.run(
                [str(args.probe.resolve())],
                input="".join(
                    f"{data.encode().hex()}\t{registry}\t{shape}\t{counter}\t{mask}"
                    + ("" if prefix is None else f"\t{prefix}")
                    + "\n"
                    for data in batch
                    for prefix in [*range(6), None]
                ),
                text=True,
                capture_output=True,
                check=True,
            )
            lines = completed.stdout.splitlines()
            assert len(lines) == len(batch) * 7
            for index, data in enumerate(batch):
                checked += 1
                chain = lines[index * 7 : index * 7 + 7]
                if chain[-1] == "delegate":
                    delegated += 1
                    assert all(line == "delegate" for line in chain)
                    continue
                chain = [line for line in chain if line != "no-checkpoint"]
                md = reference.ZulipMarkdown(linkifiers=[], linkifiers_key=0, email_gateway=False)
                md.zulip_db_data = None
                md.zulip_rendering_result = reference.MessageRenderingResult(
                    "sentinel",
                    True,
                    False,
                    {901},
                    {902},
                    {"word"},
                    {"url"},
                    {903},
                    ["old", "old"],
                    {"spinner"},
                    True,
                    True,
                )
                initial_metadata = copy.deepcopy(md.zulip_rendering_result)
                original_registry = md.inlinePatterns
                md.inlinePatterns = util.Registry()
                for priority, name in enumerate(orders[registry]):
                    md.inlinePatterns.register(original_registry[name], name, 100 - priority)
                processor = InlineProcessor(md)
                md.treeprocessors.register(processor, "inline", 25)
                originals = tree(data, shape)
                md.htmlStash.html_counter = counter
                md.htmlStash.rawHtmlBlocks.append("seed")
                expected: list[object] = []

                def render(
                    body: str,
                    *,
                    is_inline: bool,
                    expected: list[object] = expected,
                    processor: InlineProcessor = processor,
                    root: Element = originals[0],
                    md: "ZulipMarkdown" = md,
                    mask: int = mask,
                ) -> str | None:
                    assert is_inline
                    call = len(expected)
                    expected.append(capture(root, processor, md))
                    return (
                        ("" if call == 0 else f"<math>{call} &amp; $$raw$$</math>")
                        if mask & (1 << call)
                        else None
                    )

                with (
                    patch.object(reference, "render_tex", render),
                    patch.object(reference, "allowed_schemes", ["http", "https", "mailto"]),
                ):
                    processor.run(originals[0], ["outer"])
                expected.append(capture(originals[0], processor, md))
                assert md.zulip_rendering_result == initial_metadata
                calls += len(expected) - 1
                assert len(chain) == len(expected)
                registry_state = cast(RegistryInternals, md.inlinePatterns)
                sorted_state = cast(bool, registry_state._is_sorted)
                ordered = registry_state._priority[:]
                originals = tree(data, shape)
                identities = [(id(node), id(node.attrib)) for node in originals]
                processor = InlineProcessor(md)
                md.htmlStash.rawHtmlBlocks[:] = ["seed"]
                md.htmlStash.html_counter = counter
                registry_state._priority.reverse()
                registry_state._is_sorted = False
                committer = constructor(processor, originals, ordered)
                stash_identity = parent_identity = None
                for step, (line, wanted) in enumerate(zip(chain, expected, strict=True)):
                    # The diagnostic transport omits the registry-used bit; its
                    # decision is checked by the existing pure-Rust oracle.
                    plan, blocks = decode(line, sorted_state)
                    committer.apply(plan, [], False, blocks)
                    if args.mutant_duplicate_html:
                        md.htmlStash.rawHtmlBlocks.extend(blocks)
                    states += 1
                    actual = capture(originals[0], processor, md)
                    if step:
                        assert id(processor.stashed_nodes) == stash_identity
                        assert id(processor.parent_map) == parent_identity
                    stash_identity = id(processor.stashed_nodes)
                    parent_identity = id(processor.parent_map)
                    assert identities == [(id(node), id(node.attrib)) for node in originals]
                    assert md.zulip_rendering_result == initial_metadata
                    if actual != wanted:
                        differences.append(
                            {
                                "data": data,
                                "registry": registry,
                                "shape": shape,
                                "mask": mask,
                                "step": step,
                                "actual": repr(actual),
                                "expected": repr(wanted),
                            }
                        )
                        break
                if len(differences) >= 10:
                    break
            if len(differences) >= 10:
                break
        if len(differences) >= 10:
            break
    assert extension.__file__ is not None
    paths = [
        Path(__file__),
        root / "rust-backend/src/python_inline_checkpoint.rs",
        Path(args.probe),
        Path(extension.__file__).parent / "_zulip_backend.abi3.so",
    ]
    report = {
        "seed": args.seed,
        "checked": checked,
        "delegated": delegated,
        "states": states,
        "renderer_calls": calls,
        "differences": differences,
        "sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"sha256", "differences"}}
        )
    )
    if differences:
        raise SystemExit("host checkpoints differ from Python; see report")


if __name__ == "__main__":
    main()
