# ruff: noqa: INP001
import argparse
import copy
import hashlib
import inspect
import itertools
import json
import os
import platform
import random
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch
from xml.etree.ElementTree import Element, SubElement

from check_media_plan_parity import decode_plan, snapshot
from markdown import util
from markdown.treeprocessors import InlineProcessor


def cases(seed: int, count: int) -> Iterator[tuple[str, bool | None]]:
    for text in [
        "",
        "plain فارسی",
        "#**dev tools** #**hidden**",
        "#**dev tools>t@1**",
        "#**dev tools>t** #**dev tools>unknown**",
        "#**empty>** #**empty>@001**",
        "#**empty>no** #**empty>no@1**",
        "#**<نام &>>topic<&**",
        "#**dev tools>@0001** #**dev tools>t@۱٢3**",
        "#**hidden>t** #**dev tools>t**",
        "#**prefix #**dev tools>t**",
        "[#**dev tools**] <#**dev tools**",
        "**#**dev tools>t@1****",
        "`#**dev tools>t@1**` #**dev tools>t@1**",
        "[label #**dev tools>t**](https://example/a)",
        "[#**dev tools>**](https://example/a)",
        "#**dev tools>t@** #**dev tools>t@²** #**dev tools>t@1\n**",
        "#**dev tools>** #**empty>@۰۰1**",
        "\x02klzzwxh:bad\x03 #**empty>**",
    ]:
        yield text, True
    yield "#**empty>** [bad](https://例.example/a)", False
    yield "#**unknown>t** " * 10_000, None
    for point in range(0x110000):
        char = chr(point)
        if char.isdecimal():
            yield f"#**dev tools>t@00{char}**", True
    rng = random.Random(seed)
    atoms = [
        "#**dev tools**",
        "#**dev tools>t@1**",
        "#**dev tools>t**",
        "#**empty>**",
        "#**empty>@۱٢3**",
        "#**empty>no**",
        "#**empty>no@1**",
        "#**hidden>t**",
        "#**",
        "**",
        "*",
        "#",
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
        "]",
        "`",
        "**bold**",
        "[label](https://example/a)",
        "#**dev tools>t@1**",
        "\x02klzzwxh:bad\x03",
    ]
    for _ in range(count):
        yield "".join(rng.choices(atoms, k=rng.randrange(30))), None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare native channel graph planning with the Python inline engine"
    )
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=962831)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")
    import django

    django.setup()
    from zerver.lib import markdown as reference
    from zerver.lib.markdown import MessageRenderingResult
    from zerver.lib.mention import ChannelTopicInfo

    assert reference.channel_links._rust_channel_spans is None, "Run without Rust flags"
    corpus = list(cases(args.seed, args.cases))
    digits = "".join(chr(p) for p in range(128, 0x110000) if chr(p).isdecimal())
    checked = 0
    counts = dict.fromkeys(
        ["accepted", "delegated", "channel", "topic", "message", "empty_topic"], 0
    )
    failures: list[dict[str, object]] = []
    with (
        patch.object(reference.Message, "EMPTY_TOPIC_FALLBACK_NAME", "<empty & **نام**>"),
        patch.object(reference, "allowed_schemes", ["http", "https", "mailto"]),
    ):
        for context, registry, atomic, excluded in itertools.product(
            range(2), ["normal", "reverse"], [False, True], [False, True]
        ):
            for offset in range(0, len(corpus), 1024):
                batch = corpus[offset : offset + 1024]
                result = subprocess.run(
                    [str(args.probe.resolve())],
                    input=f"digits\t{digits.encode().hex()}\n"
                    + "".join(
                        f"plan\t{text.encode().hex()}\t{context}\t{registry}\t{int(atomic)}\t{int(excluded)}\n"
                        for text, _ in batch
                    ),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                lines = result.stdout.splitlines()
                assert len(lines) == len(batch), result.stderr
                for (text, required), line in zip(batch, lines, strict=True):
                    checked += 1
                    if atomic:
                        required = True
                    if line == "delegate":
                        counts["delegated"] += 1
                        if required is True:
                            failures.append({"text": text, "error": "required native acceptance"})
                        continue
                    counts["accepted"] += 1
                    if required is False:
                        failures.append({"text": text, "error": "required delegation"})
                    md = reference.ZulipMarkdown(
                        linkifiers=[], linkifiers_key=0, email_gateway=False
                    )
                    isolated = cast(SimpleNamespace, md)
                    isolated.zulip_db_data = (
                        None
                        if context == 0
                        else SimpleNamespace(
                            stream_names={
                                "dev tools": 42,
                                "empty": 17,
                                "dev tools>t@1": 99,
                                "<نام &>": 7,
                                "empty>no": 123,
                            },
                            mention_data=SimpleNamespace(
                                get_channel_info=lambda name: SimpleNamespace(
                                    topics_policy=reference.StreamTopicsPolicyEnum.empty_topic_only.value
                                    if name == "empty"
                                    else reference.StreamTopicsPolicyEnum.allow_empty_topic.value,
                                )
                            ),
                            topic_info={
                                ChannelTopicInfo("dev tools", "t"): 123,
                                ChannelTopicInfo("empty", ""): 77,
                            },
                            realm_url="https://unused.example",
                        )
                    )
                    original = md.inlinePatterns
                    md.inlinePatterns = util.Registry()
                    names = (
                        ["stream", "topic", "stream_topic_message"]
                        if registry == "reverse"
                        else ["stream_topic_message", "topic", "stream"]
                    )
                    for index, name in enumerate(
                        ["backtick", *names, "link", "strong", "emphasis", "nl"]
                    ):
                        handler = original[name]
                        if excluded and name in names:
                            handler.ANCESTOR_EXCLUDES = ["code"]
                        md.inlinePatterns.register(handler, name, 100 - index)
                    rendering = MessageRenderingResult(
                        "sentinel",
                        True,
                        False,
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
                    initial = copy.deepcopy(rendering)
                    processor = InlineProcessor(md)
                    md.treeprocessors.register(processor, "inline", 25)
                    tree = Element("div")
                    SubElement(tree, "p").text = util.AtomicString(text) if atomic else text
                    processor.run(tree, ["code" if excluded else "outer"])
                    expected = snapshot(
                        tree, processor.stashed_nodes, processor.parent_map, processor.ancestors
                    )
                    actual, paths, has_image = decode_plan(line)
                    for node in tree.iter("a"):
                        for class_name, key in [
                            ("stream", "channel"),
                            ("stream-topic", "topic"),
                            ("message-link", "message"),
                        ]:
                            counts[key] += node.get("class") == class_name
                        counts["empty_topic"] += len(node) > 0 and node[0].tag == "em"
                    if (
                        actual != expected
                        or paths
                        or has_image
                        or asdict(rendering) != asdict(initial)
                    ):
                        failures.append(
                            {
                                "text": text,
                                "context": context,
                                "registry": registry,
                                "atomic": atomic,
                                "excluded": excluded,
                                "expected": repr(expected),
                                "actual": repr(actual),
                            }
                        )
                if len(failures) > 100:
                    break
            if len(failures) > 100:
                break
    sources = [
        Path(__file__),
        args.probe,
        *(
            root / path
            for path in [
                "rust-backend/tests/check_media_plan_parity.py",
                "rust-backend/examples/channel_links.rs",
                "rust-backend/src/markdown_native_inline.rs",
                "rust-backend/src/markdown_channel_syntax.rs",
                "rust-backend/src/markdown_channel_spans.rs",
                "rust-backend/src/url_fragments.rs",
                "zerver/lib/markdown/__init__.py",
                "zerver/lib/markdown/channel_links.py",
                "zerver/lib/mention.py",
            ]
        ),
        Path(inspect.getfile(InlineProcessor)),
    ]
    report = {
        "python": platform.python_version(),
        "markdown_version": version("Markdown"),
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


if __name__ == "__main__":
    main()
