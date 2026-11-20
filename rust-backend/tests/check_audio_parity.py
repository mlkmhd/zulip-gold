# ruff: noqa: INP001
import argparse
import copy
import hashlib
import inspect
import itertools
import json
import mimetypes
import os
import platform
import posixpath
import random
import subprocess
import sys
import urllib.parse
from collections.abc import Iterator
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch
from xml.etree.ElementTree import Element

from markdown.inlinepatterns import IMAGE_LINK_RE, LinkInlineProcessor
from markdown.treeprocessors import InlineProcessor


def hx(value: str | None) -> str:
    return "-" if value is None else value.encode().hex()


def urls(seed: int, count: int) -> Iterator[tuple[str, bool | None]]:
    for value in [
        "",
        "/static/a.mp3",
        "relative/a.mp3",
        "/user_uploads/1/a.mp3",
        "https://chat.example/user_uploads/1/a.mp3",
        "https://CHAT.example/a.mp3",
        "http://chat.example/a.mp3",
        "//chat.example/a.mp3",
        "https://static.example/a.mp3",
        "https://static.example.evil/a.mp3",
        "https://uploads.example/base/a.mp3",
        "https://uploads.example/base.evil/a.mp3",
        "https://elsewhere.example/سلام.MP3?x#y",
        "https://example/.mp3",
        "https://example/...mp3",
        "https://example/a.mp3;param?x#y",
        "/a.mp3?x",
        "x:/a.mp3?x",
        "https://example/a.mp3.gz",
        "https://example/a.mp3.GZ",
        "data:audio/mpeg;base64,abc",
        "data:audio/mpeg",
        "data:;base64,abc",
        "data:foo=bar/baz,abc",
        "https://example/a.MP3.BR",
        "https://example/a.goldaudio",
        "\x00\t/foo/a.mp3\n",
    ]:
        yield value, True
    for value in ["https://例.example/a.mp3", "http://[::1]/a.mp3"]:
        yield value, False
    for extension, prefix, suffix in itertools.product(
        sorted(mimetypes.types_map),
        ["https://example/a", "https://example/.", "./a", "x:a"],
        ["", "?x#y", ";param", ".gz"],
    ):
        yield prefix + extension.swapcase() + suffix, None
    atoms = [
        "/",
        "//",
        ".",
        "..",
        "a",
        ";",
        "?",
        "#",
        "@",
        ":",
        "http:",
        "https:",
        "file:",
        "data:",
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
        ".mp3",
        ".gz",
        ".png",
    ]
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choices(atoms, k=rng.randrange(25))), None


def audios(seed: int, count: int) -> Iterator[tuple[str, int, bool | None]]:
    for src, label, title in itertools.product(
        [
            "/user_uploads/1/a.mp3",
            "/user_uploads/1/a.mp3?x#y",
            "/user_uploads/1/a.png",
            "/user_uploads/1/../a.mp3",
            "https://chat.example/user_uploads/1/a.mp3",
            "https://elsewhere.example/a.mp3",
            "https://elsewhere.example/a.png",
            "https://elsewhere.example/a.goldaudio",
            "https://static.example/a.mp3",
            "https://uploads.example/base/a.mp3",
            "javascript:/user_uploads/1/a.mp3",
            "file:///user_uploads/1/a.mp3",
            "\x02klzzwxh:0000\x03",
            "https://elsewhere.example/سلام.mp3",
        ],
        [
            "",
            "  **title**<&> \n",
            "فارسی 🌍",
            "\x02klzzwxh:0000\x03",
            "".join(chr(value) for value in range(sys.maxunicode + 1) if chr(value).isspace()),
        ],
        ["", " ''", " 'link title'"],
    ):
        yield f"پیش ![{label}](<{src}>{title}) tail", len("پیش "), True
    for value, required in [
        ("not an image", True),
        ("![unfinished", True),
        ("![x](http://[bad]/x.mp3)", False),
        ("![x](https://例.example/x.mp3)", False),
        ("![x](http://[bad]/x.mp3 '')", True),
        ("![a [b]](/user_uploads/1/a.mp3)", True),
    ]:
        yield value, 0, required
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
        "\n",
        "\t",
        "/",
        "http:",
        "https://example/",
        "/user_uploads/1/a.mp3",
        ".mp3",
        ".png",
        "javascript:",
        "فارسی",
        "🌍",
        "\x02klzzwxh:0000\x03",
        "\x02klzzwxh:bad\x03",
    ]
    rng = random.Random(seed)
    for _ in range(count):
        value = "".join(rng.choices(atoms, k=rng.randrange(30)))
        if rng.randrange(2):
            value = f"![{value}](<https://example/a.mp3>) tail"
        yield value, 0, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare native audio preparation with Python")
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=962827)
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
    from zerver.lib import subdomains
    from zerver.lib.camo import get_camo_url
    from zerver.lib.markdown import MessageRenderingResult
    from zerver.lib.mime_types import AUDIO_INLINE_MIME_TYPES, guess_type
    from zerver.lib.thumbnail import AttachmentData

    assert reference._rust_markdown_upload_paths is None, "Run without Rust flags"
    url_cases = list(urls(args.seed, args.cases))
    audio_cases = list(audios(args.seed, args.cases))
    counts = dict.fromkeys(
        [
            "accepted",
            "delegated",
            "audio",
            "rejected_audio",
            "rejected_with_attachment",
            "attachment",
            "no_attachment",
            "mime",
            "no_mime",
            "static",
            "external",
            "camo",
        ],
        0,
    )
    failures: list[dict[str, object]] = []
    checked = 0

    def wire_map(mapping: dict[str, str]) -> list[str]:
        return [
            str(len(mapping)),
            *(hx(value) for pair in sorted(mapping.items()) for value in pair),
        ]

    # Vary the actual live MIME tables, including a deployment-specific extension.
    for custom in [False, True]:
        with patch.dict(mimetypes.types_map, {".goldaudio": "audio/mpeg"} if custom else {}):
            configurations = [
                (0, "/static/", "", "secret", None),
                (1, "/static/", "https://camo.example/", "secret", None),
                (2, "https://static.example", "https://camo.example/", "ک" * 40, None),
                (
                    3,
                    "//static.example/base/",
                    "https://camo.example/",
                    "",
                    "https://uploads.example/base/",
                ),
            ]
            for context, static, camo_uri, key, upload_root in configurations:
                host = None if context == 0 else "chat.example"
                settings.STATIC_URL = static
                settings.CAMO_URI = camo_uri
                settings.CAMO_KEY = key
                settings.EXTERNAL_URI_SCHEME = "https://"
                settings.LOCAL_UPLOADS_DIR = "/unused/uploads" if upload_root is None else None
                config = [
                    "audio_policy",
                    hx(static),
                    hx("https://"),
                    hx(upload_root),
                    hx(camo_uri),
                    hx(key),
                ]
                for mapping in [
                    mimetypes.suffix_map,
                    mimetypes.encodings_map,
                    mimetypes.types_map,
                    dict.fromkeys(AUDIO_INLINE_MIME_TYPES, ""),
                    dict.fromkeys(urllib.parse.uses_params, ""),
                ]:
                    config.extend(wire_map(mapping))
                config_line = "\t".join(config) + "\n"
                for stash_kind in range(3):
                    previews: set[str] | None = None if context == 0 else set()
                    if context >= 2:
                        previews = {"1/a.png", "1/a.mp3?x#y"}
                    md = reference.ZulipMarkdown(
                        linkifiers=[], linkifiers_key=0, email_gateway=False
                    )
                    isolated = cast(SimpleNamespace, md)
                    isolated.zulip_realm = None if host is None else SimpleNamespace(host=host)
                    isolated.zulip_db_data = (
                        None
                        if previews is None
                        else SimpleNamespace(
                            realm_url="https://chat.example",
                            user_upload_previews=AttachmentData(previews, {}),
                        )
                    )
                    stash: dict[str, str] | None = None if stash_kind == 0 else {}
                    if stash_kind == 2:
                        stash = {"0000": "\x02klzzwxh:0001\x03", "0001": "/user_uploads/1/a.mp3"}
                    if stash is None:
                        md.treeprocessors.deregister("inline")
                    else:
                        inline = md.treeprocessors["inline"]
                        assert isinstance(inline, InlineProcessor)
                        inline.stashed_nodes = cast(dict[str, str | Element], stash)
                    processor = reference.AudioInlineProcessor(IMAGE_LINK_RE, md)
                    prefix = [
                        str(int(custom)),
                        hx(None if previews is None else "https://chat.example"),
                        hx(host),
                        ",".join(reference.allowed_schemes),
                        ",".join(urllib.parse.uses_netloc),
                        "-" if previews is None else str(len(previews)),
                    ]
                    settings.ENABLE_FILE_LINKS = custom
                    for path in sorted(previews or []):
                        prefix.extend([hx(path), hx("0"), hx("0"), "-"])
                    prefix.append("-" if stash is None else str(len(stash)))
                    for name, value in (stash or {}).items():
                        prefix.extend([hx(name), hx(value)])
                    suffix = "\t".join(prefix)
                    requests = [("audio", *item) for item in audio_cases]
                    if stash_kind == 0:
                        requests += [
                            (mode, url, 0, required)
                            for mode in ["mime", "static", "camo"]
                            for url, required in url_cases
                        ]
                    with patch.object(
                        subdomains, "get_public_upload_root_url", return_value=upload_root
                    ):
                        for offset in range(0, len(requests), 2048):
                            batch = requests[offset : offset + 2048]
                            wire = config_line + "".join(
                                f"{mode}\t{hx(data)}\t{hx(host) if mode == 'static' else len(data[:start].encode())}"
                                f"\t{suffix}\n"
                                for mode, data, start, _ in batch
                            )
                            output = subprocess.run(
                                [str(args.probe.resolve())],
                                input=wire,
                                text=True,
                                capture_output=True,
                                check=True,
                            )
                            lines = output.stdout.splitlines()
                            assert len(lines) == len(batch), output.stderr
                            for (mode, data, start, required), line in zip(
                                batch, lines, strict=True
                            ):
                                checked += 1
                                # Camo hashes raw bytes and does not parse authorities.
                                if mode == "camo":
                                    required = True
                                # Non-ASCII MIME suffixes are deliberately delegated.
                                if mode == "mime" and required is True:
                                    required = None
                                if line == "delegate":
                                    counts["delegated"] += 1
                                    if required is True:
                                        failures.append(
                                            {
                                                "mode": mode,
                                                "data": data,
                                                "error": "required native acceptance",
                                            }
                                        )
                                    continue
                                counts["accepted"] += 1
                                if required is False:
                                    failures.append(
                                        {"mode": mode, "data": data, "error": "required delegation"}
                                    )
                                fields = line.split("\t")
                                if mode == "mime":
                                    actual: object = (
                                        None if line == "-" else bytes.fromhex(fields[1]).decode()
                                    )
                                    expected: object = guess_type(data)[0]
                                    counts["mime" if actual is not None else "no_mime"] += 1
                                elif mode == "static":
                                    actual = line == "1"
                                    expected = subdomains.is_static_or_current_realm_url(
                                        data, isolated.zulip_realm
                                    )
                                    counts["static" if actual else "external"] += 1
                                elif mode == "camo":
                                    actual = bytes.fromhex(fields[1]).decode()
                                    expected = get_camo_url(data)
                                    counts["camo"] += 1
                                else:
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
                                    before = copy.deepcopy(rendering)
                                    match = processor.getCompiledRegExp().match(data, start)
                                    element, _, end = (
                                        (None, None, None)
                                        if match is None
                                        else processor.handleMatch(match, data)
                                    )
                                    expected_element = None
                                    if element is not None:
                                        assert isinstance(element, Element) and end is not None
                                        assert (
                                            element.text is None
                                            and element.tail is None
                                            and not list(element)
                                        )
                                        expected_element = [
                                            len(data[:end].encode()),
                                            list(element.attrib.items()),
                                        ]
                                    actual_path = (
                                        None
                                        if fields[1] == "-"
                                        else bytes.fromhex(fields[1]).decode()
                                    )
                                    actual_element = (
                                        None
                                        if fields[2] == "-"
                                        else [
                                            int(fields[2]),
                                            [
                                                (
                                                    bytes.fromhex(k).decode(),
                                                    bytes.fromhex(v).decode(),
                                                )
                                                for k, v in zip(
                                                    fields[3::2], fields[4::2], strict=True
                                                )
                                            ],
                                        ]
                                    )
                                    if actual_path is not None:
                                        before.potential_attachment_path_ids.append(actual_path)
                                    actual = [actual_element, asdict(before)]
                                    expected = [expected_element, asdict(rendering)]
                                    counts[
                                        "audio" if actual_element is not None else "rejected_audio"
                                    ] += 1
                                    counts[
                                        "attachment" if actual_path is not None else "no_attachment"
                                    ] += 1
                                    counts["rejected_with_attachment"] += int(
                                        actual_element is None and actual_path is not None
                                    )
                                if actual != expected:
                                    failures.append(
                                        {
                                            "mode": mode,
                                            "data": data,
                                            "context": context,
                                            "stash": stash_kind,
                                            "custom": custom,
                                            "actual": repr(actual),
                                            "expected": repr(expected),
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
        root / "rust-backend/src/markdown_media.rs",
        root / "rust-backend/src/markdown_links.rs",
        root / "rust-backend/src/markdown_link_syntax.rs",
        root / "rust-backend/examples/media.rs",
        root / "zerver/lib/markdown/__init__.py",
        root / "zerver/lib/mime_types.py",
        root / "zerver/lib/camo.py",
        root / "zerver/lib/subdomains.py",
        Path(inspect.getfile(LinkInlineProcessor)),
        Path(inspect.getfile(mimetypes)),
        Path(inspect.getfile(posixpath)),
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
    assert not failures, f"{len(failures)} mismatches; see {args.report}"
    assert all(counts.values()), "Missing outcome coverage"


if __name__ == "__main__":
    main()
