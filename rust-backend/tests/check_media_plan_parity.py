# ruff: noqa: INP001
import argparse
import copy
import hashlib
import inspect
import json
import mimetypes
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

from markdown import util
from markdown.treeprocessors import InlineProcessor


def cases(seed: int, count: int) -> Iterator[tuple[str, bool | None]]:
    yield "", True
    yield "plain فارسی", True
    for value in [
        "![picture](/user_uploads/1/picture) ![voice](/user_uploads/1/voice)",
        "![both](/user_uploads/1/both) ![both](/user_uploads/1/both)",
        "![picture](/user_uploads/1/picture) ![voice](/user_uploads/1/voice) ![voice](/user_uploads/1/voice)",
        "![unknown](/user_uploads/1/unknown)",
        "![empty](/user_uploads/)",
        "![audio](https://elsewhere.example/a.mp3)",
        "![x](/user_uploads/1/sound.mp3?x#y)",
        "![raw **label**](/user_uploads/1/picture '')",
        "[link](https://example/a) ![x](/user_uploads/1/picture)",
        "![a [link](https://example/b)](/user_uploads/1/picture)",
        "![a [link](https://example/b)](/user_uploads/1/voice)",
        "![x](javascript:/user_uploads/1/picture) ![y](/user_uploads/1/voice)",
        "![x](https://chat.example/user_uploads/1/picture)",
        "![x](https://chat.example/user_uploads/1/a.mp3)",
        "[Ada | excerpt](#narrow/channel/1/topic/t/near/2 'zulip-message-reply') ![x](/user_uploads/1/picture)",
        "\x02klzzwxh:bad\x03 ![x](/user_uploads/1/picture)",
    ]:
        yield value, True
    yield "![x](/user_uploads/1/picture) ![bad](https://例.example/a)", False
    yield "![x](/user_uploads/1/picture) ![bad](http://[bad]/a)", False
    yield "![x](/user_uploads/1/picture) " + "![" * 20_000, False
    atoms = [
        "![x](/user_uploads/1/picture)",
        "![x](/user_uploads/1/voice)",
        "![x](/user_uploads/1/both)",
        "![x](/user_uploads/1/unknown)",
        "![x](https://other.example/a.mp3)",
        "[x](https://example/x)",
        "![",
        "[",
        "]",
        "(",
        ")",
        "<",
        ">",
        "'",
        '"',
        " ",
        "\n",
        "\t",
        "/",
        "javascript:",
        "فارسی",
        "🌍",
        "**",
        "`",
        "\x02klzzwxh:bad\x03",
    ]
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choices(atoms, k=rng.randrange(25))), None


def snapshot(
    root: Element,
    stash: dict[str, Element | str],
    parents: dict[Element, Element],
    ancestors: list[str],
) -> object:
    nodes = [root]
    indices = {root: 0}

    def index(node: Element) -> int:
        if node not in indices:
            indices[node] = len(nodes)
            nodes.append(node)
        return indices[node]

    def text(value: str | None) -> tuple[str | None, bool]:
        return value, isinstance(value, util.AtomicString)

    # Include detached stash and parent-map nodes, preserving dictionary order.
    stashed = [
        (key, text(value) if isinstance(value, str) else index(value))
        for key, value in stash.items()
    ]
    parent_map = [(index(node), index(parent)) for node, parent in parents.items()]
    tree = [
        (
            node.tag,
            text(node.text),
            text(node.tail),
            list(node.attrib.items()),
            [index(child) for child in node],
        )
        for node in nodes
    ]
    return tree, stashed, parent_map, ancestors


def decode_plan(line: str) -> tuple[object, list[str], bool]:
    fields = line.split("\t")

    def restore(value: str) -> str | None:
        if value == "-":
            return None
        decoded = bytes.fromhex(value[1:]).decode()
        return util.AtomicString(decoded) if value[0] == "1" else decoded

    specs = [field.split(",") for field in fields[6:]]
    nodes = [Element(bytes.fromhex(spec[0]).decode()) for spec in specs]
    for node, spec in zip(nodes, specs, strict=True):
        node.text = restore(spec[2])
        node.tail = restore(spec[3])
        node[:] = [nodes[int(child)] for child in spec[4].split(":") if child]
        for pair in spec[5].split(";") if spec[5] else []:
            key, value = pair.split(":")
            node.set(bytes.fromhex(key).decode(), bytes.fromhex(value).decode())
    stash: dict[str, Element | str] = {}
    for index, value in enumerate(fields[5].split(",") if fields[5] else []):
        item = nodes[int(value[1:])] if value[0] == "n" else restore(value[1:])
        assert item is not None
        stash[f"{index:04}"] = item
    parents = {}
    for pair in fields[4].split(",") if fields[4] else []:
        child, parent = pair.split(":")
        parents[nodes[int(child)]] = nodes[int(parent)]
    ancestors = [bytes.fromhex(value).decode() for value in fields[3].split(",") if value]
    paths = [bytes.fromhex(value[1:]).decode() for value in fields[2].split(",") if value]
    return snapshot(nodes[0], stash, parents, ancestors), paths, fields[1] == "1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare native media graph and metadata planning")
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=962828)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")
    import django

    django.setup()
    from django.conf import settings

    from zerver.lib import markdown as reference
    from zerver.lib.markdown import MessageRenderingResult
    from zerver.lib.thumbnail import AttachmentData, MarkdownImageMetadata

    settings.STATIC_URL = "/static/"
    settings.EXTERNAL_URI_SCHEME = "https://"
    settings.LOCAL_UPLOADS_DIR = "/unused/uploads"
    settings.ENABLE_FILE_LINKS = False
    settings.CAMO_URI = "https://camo.example/"
    settings.CAMO_KEY = "secret"
    assert reference._rust_markdown_upload_paths is None, "Run without Rust flags"
    all_cases = list(cases(args.seed, args.cases))
    counts = dict.fromkeys(
        [
            "accepted",
            "delegated",
            "image",
            "audio",
            "attachment",
            "duplicate_attachment",
            "rejected_with_attachment",
        ],
        0,
    )
    failures: list[dict[str, object]] = []
    checked = 0
    with (
        patch.object(reference, "allowed_schemes", ["http", "https", "file", "mailto"]),
        patch.dict(mimetypes.types_map, {".mp3": "audio/mpeg", ".png": "image/png"}, clear=True),
        patch.dict(mimetypes.suffix_map, {}, clear=True),
        patch.dict(mimetypes.encodings_map, {".gz": "gzip"}, clear=True),
    ):
        for context in range(3):
            for registry in ["normal", "reverse"]:
                for atomic in [False, True]:
                    for offset in range(0, len(all_cases), 2048):
                        batch = all_cases[offset : offset + 2048]
                        wire = "".join(
                            f"{data.encode().hex()}\t{context}\t{registry}\t{int(atomic)}\n"
                            for data, _ in batch
                        )
                        process = subprocess.run(
                            [str(args.probe.resolve())],
                            input=wire,
                            text=True,
                            capture_output=True,
                            check=True,
                        )
                        lines = process.stdout.splitlines()
                        assert len(lines) == len(batch), process.stderr
                        for (data, required), line in zip(batch, lines, strict=True):
                            checked += 1
                            if atomic:
                                required = True
                            if line == "delegate":
                                counts["delegated"] += 1
                                if required is True:
                                    failures.append(
                                        {"data": data, "error": "required native acceptance"}
                                    )
                                continue
                            counts["accepted"] += 1
                            if required is False:
                                failures.append({"data": data, "error": "required delegation"})
                            md = reference.ZulipMarkdown(
                                linkifiers=[], linkifiers_key=0, email_gateway=False
                            )
                            isolated = cast(SimpleNamespace, md)
                            isolated.zulip_realm = (
                                None if context == 0 else SimpleNamespace(host="chat.example")
                            )
                            isolated.zulip_db_data = (
                                None
                                if context == 0
                                else SimpleNamespace(
                                    realm_url="https://chat.example",
                                    user_upload_previews=AttachmentData(
                                        set()
                                        if context == 1
                                        else {"1/voice", "1/both", "1/sound.mp3?x#y"},
                                        {}
                                        if context == 1
                                        else {
                                            path: MarkdownImageMetadata(
                                                None, False, 10, 20, "image/png"
                                            )
                                            for path in ["1/picture", "1/both"]
                                        },
                                    ),
                                )
                            )
                            original = md.inlinePatterns
                            md.inlinePatterns = util.Registry()
                            names = (
                                ["link", "audio", "image"]
                                if registry == "normal"
                                else ["link", "image", "audio"]
                            )
                            for index, name in enumerate(names):
                                md.inlinePatterns.register(original[name], name, 10 - index)
                            rendering = MessageRenderingResult(
                                "sentinel",
                                True,
                                False,
                                {1},
                                {2},
                                {"word"},
                                {"url"},
                                {3},
                                ["sentinel", "sentinel"],
                                {"spinner"},
                                checked % 2 == 0,
                                True,
                            )
                            isolated.zulip_rendering_result = rendering
                            actual_result = copy.deepcopy(rendering)
                            processor = InlineProcessor(md)
                            md.treeprocessors.register(processor, "inline", 25)
                            tree = Element("div")
                            SubElement(tree, "p").text = util.AtomicString(data) if atomic else data
                            processor.run(tree, ["outer"])
                            expected = snapshot(
                                tree,
                                processor.stashed_nodes,
                                processor.parent_map,
                                processor.ancestors,
                            )
                            actual, paths, has_image = decode_plan(line)
                            actual_result.potential_attachment_path_ids.extend(paths)
                            actual_result.has_image |= has_image
                            counts["attachment"] += bool(paths)
                            counts["duplicate_attachment"] += len(set(paths)) != len(paths)
                            counts["image"] += has_image
                            counts["audio"] += any(node.tag == "audio" for node in tree.iter())
                            counts["rejected_with_attachment"] += (
                                bool(paths)
                                and not has_image
                                and not any(node.tag in {"img", "audio"} for node in tree.iter())
                            )
                            if actual != expected or asdict(actual_result) != asdict(rendering):
                                failures.append(
                                    {
                                        "data": data,
                                        "context": context,
                                        "registry": registry,
                                        "atomic": atomic,
                                        "expected": repr((expected, rendering)),
                                        "actual": repr((actual, actual_result)),
                                    }
                                )
                        if len(failures) > 100:
                            break
                    if len(failures) > 100:
                        break
                if len(failures) > 100:
                    break
            if len(failures) > 100:
                break
    sources = [
        Path(__file__).resolve(),
        args.probe.resolve(),
        root / "rust-backend/src/markdown_native_inline.rs",
        root / "rust-backend/src/markdown_media.rs",
        root / "rust-backend/src/markdown_links.rs",
        root / "rust-backend/src/markdown_link_syntax.rs",
        root / "rust-backend/examples/media_plan.rs",
        root / "zerver/lib/markdown/__init__.py",
        Path(inspect.getfile(InlineProcessor)),
        Path(mimetypes.__file__),
    ]
    report = {
        "python": platform.python_version(),
        "markdown_version": version("Markdown"),
        "seed": args.seed,
        "random_cases": args.cases,
        "checked": checked,
        "counts": counts,
        "hashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        "failures": failures,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"hashes", "failures"}}
        )
    )
    assert not failures, f"{len(failures)} mismatches; see {args.report}"
    assert all(counts.values()), "Missing outcome coverage"


if __name__ == "__main__":
    main()
