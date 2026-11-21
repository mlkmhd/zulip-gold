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
from pathlib import Path
from typing import cast
from unittest.mock import patch
from xml.etree.ElementTree import Element, SubElement

from check_media_plan_parity import decode_plan, snapshot
from markdown import util
from markdown.treeprocessors import InlineProcessor


class RendererError(Exception):
    pass


def cases(seed: int, count: int) -> Iterator[str]:
    yield from [
        "",
        "plain فارسی",
        "$$x$$ $$x$$ $$α$$",
        "$$a$$ $$b$$ $$c$$ $$d$$ $$e$$ $$f$$",
        "$$a$$ $$b$$ $$c$$ $$d$$ $$e$$ $$f$$ $$g$$",
        "$$<script>&amp;$$ &lt; $$two$$ &gt;",
        "`$$code$$` **$$strong$$** ***$$both$$*** *$$em$$* ~~$$del$$~~",
        '[label](https://example.com/$$α$$ "$$x$$")',
        "[label](https://$$é$$)",
        "[label](https://example.com/$$x$$) $$later$$",
        "[$$label$$](javascript:alert(1))",
        "[$$label$$](https://example.com) $$tail$$",
        "$$x\\$$ : $$x\\$$$ : $$x\\$y$$",
        "$$x\ny$$ $$\r$$ $$x$$\n",
        "\x02klzzwxh:0000\x03 $$x$$ \x02klzzwxh:bad\x03",
        "[\x02klzzwxh:0000\x03](https://example.com) $$x$$",
        "\x02wzxhzdk:7\x03 $$x$$ &amp;",
        "\u0301$$α$$📝 $$名$$ سلام$$x$$ $$x$$²",
    ]
    atoms = [
        "$$x$$",
        "$$α$$",
        "$$<&>$$",
        "$$x\\$y$$",
        "$$x\\$$$",
        "$$",
        "[label](https://example.com/$$x$$)",
        "[$$x$$](https://example.com)",
        "[x](https://例.example)",
        "[x](javascript:evil)",
        "[",
        "]",
        "(",
        ")",
        "&amp;",
        "&#31;",
        "&not_an_entity;",
        "`",
        "**",
        "***",
        "~~",
        "*",
        " ",
        "\n",
        "\r",
        "\t",
        "\x1c",
        "\u2003",
        "فارسی",
        "📝",
        "_",
        "\x02klzzwxh:0000\x03",
        "\x02klzzwxh:9999\x03",
        "\x02wzxhzdk:7\x03",
    ]
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choices(atoms, k=rng.randrange(16)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare every selected TeX plan with live Python")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=962856)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mutant-error-class", action="store_true")
    parser.add_argument("--checkpoints", action="store_true")
    parser.add_argument("--mutant-clear-tail", action="store_true")
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    assert not any(
        name.startswith("ZULIP_RUST_") and value == "1" for name, value in os.environ.items()
    )
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")
    import django

    django.setup()
    from django.conf import settings

    from zerver.lib import markdown as reference
    from zerver.lib.markdown.native_inline import RegistryInternals

    assert reference._rust_tex_handlers is None
    settings.ENABLE_FILE_LINKS = False
    source = list(cases(args.seed, args.cases))
    differences: list[dict[str, object]] = []
    checked = accepted = delegated = rendered = no_checkpoint = 0
    for registry, shape, mask, prefix in itertools.product(
        ["normal", "reverse", "last"],
        range(5),
        [0, 63, 21, 42],
        range(6) if args.checkpoints else [None],
    ):
        order = {
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
        }[registry]
        for offset in range(0, len(source), 256):
            batch = source[offset : offset + 256]
            counter = [0, 7, 9999, 1_000_000][(offset // 256) % 4]
            completed = subprocess.run(
                [str(args.probe.resolve())],
                input="".join(
                    f"{data.encode().hex()}\t{registry}\t{shape}\t{counter}\t{mask}"
                    + (f"\t{prefix}" if prefix is not None else "")
                    + "\n"
                    for data in batch
                ),
                text=True,
                capture_output=True,
                check=True,
            )
            lines = completed.stdout.splitlines()
            assert len(lines) == len(batch), completed.stderr
            for data, line in zip(batch, lines, strict=True):
                checked += 1
                if line == "no-checkpoint":
                    no_checkpoint += 1
                    continue
                if line == "delegate":
                    delegated += 1
                    assert data not in {"", "plain فارسی", "$$x$$ $$x$$ $$α$$"}
                    continue
                accepted += 1
                graph, extra = line.split("\ttex\t")
                calls_text, blocks_text = extra.split("\t")

                def decode_list(value: str) -> list[str]:
                    return [bytes.fromhex(item[1:]).decode() for item in value.split(",") if item]

                calls = decode_list(calls_text)
                blocks = decode_list(blocks_text)
                if args.mutant_error_class:
                    graph = graph.replace(b"tex-error".hex(), b"tex_error".hex())
                if args.mutant_clear_tail:
                    fields = graph.split("\t")
                    for index in range(6, len(fields)):
                        node = fields[index].split(",")
                        node[3] = "-"
                        fields[index] = ",".join(node)
                    graph = "\t".join(fields)
                actual, paths, has_image = decode_plan(graph)
                assert not paths and not has_image
                md = reference.ZulipMarkdown(linkifiers=[], linkifiers_key=0, email_gateway=False)
                md.zulip_db_data = None
                md.zulip_rendering_result = reference.MessageRenderingResult(
                    rendered_content="sentinel",
                    mentions_topic_wildcard=True,
                    mentions_stream_wildcard=False,
                    mentions_user_ids={1},
                    mentions_user_group_ids={2},
                    alert_words={"word"},
                    links_for_preview={"url"},
                    user_ids_with_alert_words={3},
                    potential_attachment_path_ids=["a", "a"],
                    thumbnail_spinners={"spinner"},
                    has_image=True,
                    has_link=True,
                )
                original = md.inlinePatterns
                md.inlinePatterns = util.Registry()
                for priority, name in enumerate(order):
                    md.inlinePatterns.register(original[name], name, 100 - priority)
                md.htmlStash.html_counter = counter
                processor = InlineProcessor(md)
                md.treeprocessors.register(processor, "inline", 25)
                tree = Element("div")
                paragraph = SubElement(tree, "p")
                paragraph.text = data
                if shape == 1:
                    paragraph.text = "$$leading$$ &amp;"
                    paragraph.tail = data
                    SubElement(tree, "p").text = "$$trailing$$"
                elif shape == 2:
                    child = SubElement(paragraph, "span")
                    child.text = "$$child$$"
                    child.tail = "$$tail$$"
                elif shape == 3:
                    paragraph.text = util.AtomicString(data)
                elif shape == 4:
                    shared = SubElement(paragraph, "span")
                    shared.text = "$$shared$$"
                    shared.tail = "$$shared-tail$$"
                    other = SubElement(tree, "p")
                    other.text = "$$other$$"
                    other.append(shared)
                invoked: list[str] = []
                metadata = copy.deepcopy(md.zulip_rendering_result)

                def render(
                    body: str,
                    *,
                    is_inline: bool,
                    invoked: list[str] = invoked,
                    mask: int = mask,
                    prefix: int | None = prefix,
                ) -> str | None:
                    assert is_inline
                    index = len(invoked)
                    invoked.append(body)
                    if index == prefix:
                        raise RendererError(index, body)
                    return (
                        ("" if index == 0 else f"<math>{index} &amp; $$raw$$</math>")
                        if mask & (1 << index)
                        else None
                    )

                with (
                    patch.object(reference, "render_tex", render),
                    patch.object(reference, "allowed_schemes", ["http", "https", "mailto"]),
                ):
                    try:
                        processor.run(tree, ["outer"])
                    except RendererError as error:
                        assert prefix is not None
                        assert error.args == (prefix, invoked[-1])
                        assert len(invoked) == prefix + 1
                    else:
                        assert prefix is None
                if prefix is not None:
                    registry_state = cast(RegistryInternals, md.inlinePatterns)
                    assert registry_state._is_sorted is True
                    assert [name for name, _priority in registry_state._priority] == order
                assert md.zulip_rendering_result == metadata
                rendered += len(invoked)
                expected = snapshot(
                    tree, processor.stashed_nodes, processor.parent_map, processor.ancestors
                )
                if (actual, calls, blocks, counter + len(blocks)) != (
                    expected,
                    invoked,
                    md.htmlStash.rawHtmlBlocks,
                    md.htmlStash.html_counter,
                ):
                    differences.append(
                        {
                            "data": data,
                            "registry": registry,
                            "shape": shape,
                            "mask": mask,
                            "prefix": prefix,
                            "actual": repr(actual),
                            "expected": repr(expected),
                            "calls": calls,
                            "invoked": invoked,
                            "blocks": blocks,
                            "reference_blocks": md.htmlStash.rawHtmlBlocks,
                        }
                    )
                    if len(differences) >= 10:
                        break
            if len(differences) >= 10:
                break
        if len(differences) >= 10:
            break
    hashed = [
        Path(__file__),
        root / "rust-backend/src/markdown_tex_preflight.rs",
        root / "rust-backend/src/markdown_native_inline.rs",
        root / "rust-backend/src/markdown_tex_syntax.rs",
        root / "rust-backend/examples/tex_preflight.rs",
        Path(args.probe),
        root / "zerver/lib/markdown/__init__.py",
        root / "zerver/lib/markdown/inline_code.py",
        Path(__file__).with_name("check_media_plan_parity.py"),
        Path(util.__file__),
        Path(inspect.getfile(InlineProcessor)),
    ]
    report = {
        "seed": args.seed,
        "python": platform.python_version(),
        "checked": checked,
        "accepted": accepted,
        "delegated": delegated,
        "no_checkpoint": no_checkpoint,
        "checkpoints": args.checkpoints,
        "reference_renderer_calls": rendered,
        "differences": differences,
        "sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in hashed},
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"differences", "sha256"}},
            sort_keys=True,
        )
    )
    if differences:
        raise SystemExit("TeX preflight differs from Python; see report")


if __name__ == "__main__":
    main()
