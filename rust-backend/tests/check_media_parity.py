# ruff: noqa: INP001
import argparse
import hashlib
import inspect
import itertools
import json
import os
import platform
import random
import subprocess
import sys
import urllib.parse
from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from xml.etree.ElementTree import Element

from markdown.inlinepatterns import IMAGE_LINK_RE, ImageInlineProcessor
from markdown.treeprocessors import InlineProcessor


def upload_cases(seed: int, count: int) -> Iterator[tuple[str, bool | None]]:
    for value in [
        "",
        "/user_uploads/",
        "user_uploads/1/a.png",
        "/user_uploads/1/سلام.png?x#y",
        "user_uploads/1//a.png",
        "/user_uploads/1//a.png",
        "/user_uploads/1/./a.png",
        "/user_uploads/1/../a.png",
        "/user_uploads/1/..;x",
        "/user_uploads/1/a.png;",
        "/../http:/user_uploads/a",
        "/../\x01/user_uploads/a",
        "/user_uploads/1/%2e%2e/a",
        "http://chat.example/user_uploads/a",
        "http://CHAT.example/user_uploads/a",
        "http://elsewhere.example/user_uploads/a",
        "file:///user_uploads/a",
        "mailto:/user_uploads/a",
        "//chat.example/user_uploads/a",
        "///user_uploads/a",
    ]:
        yield value, True
    for value in ["https://例.example/user_uploads/a", "http://[::1]/user_uploads/a"]:
        yield value, False
    for segments in itertools.product(["", ".", "..", "a", "user_uploads", ";x", "سلام"], repeat=4):
        yield "/".join(segments), None
        yield "/" + "/".join(segments), None
    atoms = [
        "/",
        "//",
        ".",
        "..",
        "user_uploads",
        "a",
        ";",
        ";x",
        "?",
        "#",
        "@",
        ":",
        "http:",
        "https:",
        "file:",
        "chat.example",
        "[",
        "]",
        "例",
        "فارسی",
        " ",
        "\x00",
        "\x01",
        "\t",
        "\n",
        "\r",
        "%2f",
        "%20",
    ]
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choices(atoms, k=rng.randrange(25))), None


def image_cases(seed: int, count: int) -> Iterator[tuple[str, int, bool | None]]:
    for src, label, title in itertools.product(
        [
            "/user_uploads/1/a.png",
            "/user_uploads/1/a.png?x#y",
            "/user_uploads/1/سلام.png",
            "/user_uploads/1/../a.png",
            "https://chat.example/user_uploads/1/a.png",
            "https://elsewhere.example/a.png",
            "javascript:/user_uploads/1/a.png",
            "file:///user_uploads/1/a.png",
            "\x02klzzwxh:0000\x03",
        ],
        ["", "**alt**<&>", "فارسی 🌍", "\x02klzzwxh:0000\x03"],
        ["", " ''", " 'a\nb'"],
    ):
        text = f"پیش ![{label}](<{src}>{title}) tail"
        yield text, len("پیش "), True
    for value in [
        "not an image",
        "![unfinished",
        "![x](javascript:x)",
        "![x](http://[bad]/x)",
        "![x](https://例.example/x)",
        "![a [b]](/user_uploads/1/a.png)",
    ]:
        yield value, 0, not ("[bad]" in value or "例.example" in value)
    atoms = [
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
        "\t",
        "\n",
        "a",
        "/",
        "user_uploads/1/a.png",
        "http:",
        "javascript:",
        "فارسی",
        "🌍",
        "\x02klzzwxh:0000\x03",
        "\x02klzzwxh:bad\x03",
        "\x02klzzwxh:0003\x03",
    ]
    rng = random.Random(seed)
    for _ in range(count):
        value = "".join(rng.choices(atoms, k=rng.randrange(30)))
        if rng.randrange(2):
            value = f"![{value}](</user_uploads/1/a.png>) tail"
        yield value, 0, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare native image and attachment effects")
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=962826)
    parser.add_argument("--report", type=Path, required=True)
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

    assert reference._rust_markdown_upload_paths is None, "Run without Rust feature flags"
    uploads = [
        ("upload", data, 0, required) for data, required in upload_cases(args.seed, args.cases)
    ]
    images = [("image", *item) for item in image_cases(args.seed, args.cases)]
    counts = {
        "accepted": 0,
        "delegated": 0,
        "image": 0,
        "rejected_image": 0,
        "attachment": 0,
        "rejected_with_attachment": 0,
        "no_attachment": 0,
    }
    failures: list[dict[str, object]] = []
    checked = 0

    def hx(value: str | None) -> str:
        return "-" if value is None else value.encode().hex()

    for context, enabled, stash_kind in itertools.product(range(4), [False, True], range(3)):
        settings.ENABLE_FILE_LINKS = enabled
        host = None if context == 0 else "chat.example"
        previews: dict[str, MarkdownImageMetadata] | None = None if context == 0 else {}
        if context >= 2:
            previews = {
                "1/a.png": MarkdownImageMetadata(None, False, 10**40, -2, "image/png"),
                "1/سلام.png": MarkdownImageMetadata(
                    None, True, 0, 12, None if context == 2 else ""
                ),
            }
        md = reference.ZulipMarkdown(linkifiers=[], linkifiers_key=0, email_gateway=False)
        isolated = cast(SimpleNamespace, md)
        isolated.zulip_realm = None if host is None else SimpleNamespace(host=host)
        isolated.zulip_db_data = (
            None
            if previews is None
            else SimpleNamespace(
                realm_url="https://chat.example",
                user_upload_previews=AttachmentData(set(), previews),
            )
        )
        stash: dict[str, str] | None = None if stash_kind == 0 else {}
        if stash_kind == 2:
            stash = {
                "0000": "\x02klzzwxh:0001\x03",
                "0001": "/user_uploads/1/a.png",
                "0002": "javascript:x",
                "0003": " <&>فارسی ",
            }
        if stash is None:
            md.treeprocessors.deregister("inline")
        else:
            inline = md.treeprocessors["inline"]
            assert isinstance(inline, InlineProcessor)
            inline.stashed_nodes = cast(dict[str, str | Element], stash)
        processor = reference.ImageInlineProcessor(IMAGE_LINK_RE, md)
        # Upload extraction is independent of preview, file and stash policies.
        requests = images + (uploads if not enabled and stash_kind == 0 else [])
        prefix = [
            str(int(enabled)),
            hx(None if previews is None else "https://chat.example"),
            hx(host),
            ",".join(reference.allowed_schemes),
            ",".join(urllib.parse.uses_netloc),
            "-" if previews is None else str(len(previews)),
        ]
        for path_id, metadata in (previews or {}).items():
            prefix.extend(
                [
                    hx(path_id),
                    hx(str(metadata.original_width_px)),
                    hx(str(metadata.original_height_px)),
                    hx(metadata.original_content_type),
                ]
            )
        prefix.append("-" if stash is None else str(len(stash)))
        for key, value in (stash or {}).items():
            prefix.extend([hx(key), hx(value)])
        suffix = "\t".join(prefix)
        for offset in range(0, len(requests), 4096):
            batch = requests[offset : offset + 4096]
            wire = "".join(
                f"{mode}\t{hx(data)}\t{len(data[:start].encode())}\t{suffix}\n"
                for mode, data, start, _ in batch
            )
            output = subprocess.run(
                [str(args.probe.resolve())], input=wire, text=True, capture_output=True, check=True
            )
            lines = output.stdout.splitlines()
            assert len(lines) == len(batch), output.stderr
            for (mode, data, start, required), line in zip(batch, lines, strict=True):
                checked += 1
                if line == "delegate":
                    counts["delegated"] += 1
                    if required is True:
                        failures.append(
                            {"data": data, "mode": mode, "error": "required native acceptance"}
                        )
                    continue
                counts["accepted"] += 1
                if required is False:
                    failures.append({"data": data, "mode": mode, "error": "required delegation"})
                initial_image = checked % 2 == 0
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
                    initial_image,
                    True,
                )
                isolated.zulip_rendering_result = rendering
                fields = line.split("\t")
                if mode == "upload":
                    reference.maybe_add_attachment_path_id(data, md)
                    actual_path = None if line == "-" else bytes.fromhex(fields[1]).decode()
                    actual: object = [] if actual_path is None else [actual_path]
                    expected: object = rendering.potential_attachment_path_ids[2:]
                else:
                    matched = processor.getCompiledRegExp().match(data, start)
                    element, _, end = (
                        (None, None, None)
                        if matched is None
                        else processor.handleMatch(matched, data)
                    )
                    expected_image = None
                    if element is not None:
                        assert isinstance(element, Element) and end is not None
                        assert element.text is None and element.tail is None and not list(element)
                        expected_image = [len(data[:end].encode()), list(element.attrib.items())]
                    actual_path = None if fields[1] == "-" else bytes.fromhex(fields[1]).decode()
                    actual_image = (
                        None
                        if fields[2] == "-"
                        else [
                            int(fields[2]),
                            [
                                (bytes.fromhex(key).decode(), bytes.fromhex(value).decode())
                                for key, value in zip(fields[3::2], fields[4::2], strict=True)
                            ],
                        ]
                    )
                    actual = [
                        actual_image,
                        [] if actual_path is None else [actual_path],
                        initial_image or actual_image is not None,
                    ]
                    expected = [
                        expected_image,
                        rendering.potential_attachment_path_ids[2:],
                        rendering.has_image,
                    ]
                    counts["image" if actual_image is not None else "rejected_image"] += 1
                    counts["rejected_with_attachment"] += int(
                        actual_image is None and actual_path is not None
                    )
                    assert (
                        rendering.rendered_content,
                        rendering.mentions_user_ids,
                        rendering.links_for_preview,
                        rendering.thumbnail_spinners,
                        rendering.has_link,
                    ) == ("sentinel", {1}, {"url"}, {"spinner"}, True)
                counts["attachment" if actual_path is not None else "no_attachment"] += 1
                if actual != expected:
                    failures.append(
                        {
                            "data": data,
                            "mode": mode,
                            "context": context,
                            "stash_kind": stash_kind,
                            "file_enabled": enabled,
                            "expected": expected,
                            "actual": actual,
                        }
                    )
            if len(failures) > 100:
                break
        if len(failures) > 100:
            break
    sources = [
        Path(__file__).resolve(),
        args.probe.resolve(),
        root / "rust-backend/src/markdown_media.rs",
        root / "rust-backend/src/markdown_links.rs",
        root / "rust-backend/src/markdown_link_syntax.rs",
        root / "rust-backend/examples/media.rs",
        root / "zerver/lib/markdown/__init__.py",
        Path(inspect.getfile(ImageInlineProcessor)),
        Path(inspect.getfile(urllib.parse)),
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
    assert all(counts.values()), "Missing outcome coverage"
    assert not failures, f"{len(failures)} mismatches; see {args.report}"


if __name__ == "__main__":
    main()
