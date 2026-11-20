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

import markdown
from markdown.inlinepatterns import LINK_RE, LinkInlineProcessor
from markdown.treeprocessors import InlineProcessor


def urls(seed: int, count: int) -> Iterator[tuple[str, bool | None]]:
    for value in [
        "",
        "example.com",
        "//example.com/a",
        "///path",
        "#fragment",
        "/path?x#y",
        "http:relative",
        "HTTPS://Example:bad/a?#",
        "mailto:نام@example.com",
        "javascript:alert(1)",
        "JAV\tASCRIPT://x",
        "data:text/html,x",
        "file:///a b",
        "http://a\n\r\tb/path",
        "\x01example.com",
        "\x01http://example.com",
        " http://example.com",
        "https://example.com/例",
        "a@b",
        "نام@example.com",
    ]:
        yield value, True
    for value in [
        "https://例.example/x",
        "http://a／b/x",
        "http://[::1]",
        "http://x]",
        "http://[bad]",
        "javascript://[bad]",
    ]:
        yield value, False
    for a, b, c in itertools.product(
        ["", "http:", "https:", "file:", "javascript:", "custom:", "mailto:", "1:", "H+.-:"],
        ["", "/", "//", "///", "user@", "[::1]", "例", "\x01", "\t", "%20"],
        ["", "example.com", "a b", "/p", "?q", "#f", "?#", "@x", ":x", "\\host"],
    ):
        yield a + b + c, None
    atoms = [
        "a",
        "Z",
        "1",
        ":",
        "/",
        "?",
        "#",
        "@",
        "[",
        "]",
        ".",
        "+",
        "-",
        " ",
        "\t",
        "\n",
        "\r",
        "\x00",
        "\x1f",
        "\x7f",
        "例",
        "فارسی",
        "／",
        "＠",
        "%20",
        "%3a",
        "\\",
        "https:",
        "javascript:",
        "//",
    ]
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choices(atoms, k=rng.randrange(30))), None
    for point in range(128):
        char = chr(point)
        for template in ["{}http://example.com", "http://ex{}ample.com/a", "example.com/{}?q#f"]:
            yield template.format(char), None if char in "[]" else True


def load_reference(root: Path) -> dict[str, object]:
    sys.path.insert(0, str(root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")
    import django

    django.setup()
    from zerver.lib import markdown as reference

    return vars(reference)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare native URL policy and explicit links")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=962824)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be nonnegative")
    root = Path(__file__).resolve().parents[2]
    reference = load_reference(root)
    settings = cast(SimpleNamespace, reference["settings"])
    processor_class = cast(type[LinkInlineProcessor], reference["LinkInlineProcessor"])
    schemes = cast(tuple[str, ...], reference["allowed_schemes"])
    corpus = list(urls(args.seed, args.cases))
    counts = {"accepted": 0, "delegated": 0, "url": 0, "link": 0, "rejected": 0}
    failures: list[dict[str, object]] = []
    checked = 0
    for enabled, restricted in itertools.product([False, True], [False, True]):
        allowed = ("custom",) if restricted else schemes
        reference["allowed_schemes"] = allowed
        settings.ENABLE_FILE_LINKS = enabled
        for stash in [
            None,
            {},
            {
                "0000": "\x02klzzwxh:0001\x03",
                "0001": " example.com ",
                "0002": "javascript:alert(1)",
            },
        ]:
            md = markdown.Markdown()
            md.zulip_db_data = SimpleNamespace(realm_url="https://chat.example")  # type: ignore[attr-defined] # Isolated handler context.
            if stash is None:
                md.treeprocessors.deregister("inline")
            else:
                inline = md.treeprocessors["inline"]
                assert isinstance(inline, InlineProcessor)
                inline.stashed_nodes = cast(dict[str, str | Element], stash)
            processor = processor_class(LINK_RE, md)
            requests: list[tuple[str, str, bool | None]] = [
                ("sanitize", value, mandatory) for value, mandatory in corpus
            ]
            requests.extend(
                ("link", f"[**label**](<{value}> 'title') tail", None) for value, _ in corpus
            )
            for href in [
                "#narrow/dm/1/near/123",
                "https://chat.example/#narrow/channel/1/near/456",
                "https://chat.example/user_uploads/a",
                "#narrow/dm/1/near/۱۲۳",
                "\x02klzzwxh:0000\x03",
                "\x02klzzwxh:0002\x03",
            ]:
                for label, title in itertools.product(
                    [
                        "",
                        " ",
                        "Alice | hello | world",
                        "Alice &#183; hi",
                        " | hi",
                        "*atomic*",
                        "فارسی 🌍",
                    ],
                    ["", "zulip-message-reply", "a\nb"],
                ):
                    requests.append(("link", f"[{label}]({href} '{title}')", True))
            for offset in range(0, len(requests), 4096):
                batch = requests[offset : offset + 4096]
                wire = "".join(
                    f"{mode}\t{value.encode().hex()}\t0\t{int(enabled)}\t{b'https://chat.example'.hex()}\t{','.join(allowed)}\t{','.join(urllib.parse.uses_netloc)}\t{'absent' if stash is None else 'present'}"
                    + "".join(
                        f"\t{key.encode().hex()}\t{text.encode().hex()}"
                        for key, text in (stash or {}).items()
                    )
                    + "\n"
                    for mode, value, _ in batch
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
                for (mode, value, mandatory), line in zip(batch, lines, strict=True):
                    checked += 1
                    if line == "delegate":
                        counts["delegated"] += 1
                        if mandatory is True:
                            failures.append(
                                {"data": value, "mode": mode, "error": "required native acceptance"}
                            )
                        continue
                    counts["accepted"] += 1
                    if mandatory is False:
                        failures.append(
                            {"data": value, "mode": mode, "error": "required delegation"}
                        )
                    if mode == "sanitize":
                        expected = reference["sanitize_url"](value)  # type: ignore[operator] # Dynamically loaded reference function.
                        actual: object = (
                            None if line == "-" else bytes.fromhex(line.split("\t")[1]).decode()
                        )
                        counts["url" if actual is not None else "rejected"] += 1
                    else:
                        matched = processor.getCompiledRegExp().match(value)
                        assert matched is not None
                        element, _, end = processor.handleMatch(matched, value)
                        expected = None
                        if element is not None:
                            assert isinstance(element, Element) and end is not None
                            expected = [
                                len(value[:end].encode()),
                                element.get("href"),
                                element.get("title"),
                                element.text or "",
                                element.get("class") == "message-reply-link",
                                *[child.text for child in element],
                            ]
                        if line == "-":
                            actual = None
                        else:
                            fields = line.split("\t")
                            actual = [
                                int(fields[1]),
                                bytes.fromhex(fields[2]).decode(),
                                None if fields[3] == "-" else bytes.fromhex(fields[3]).decode(),
                                bytes.fromhex(fields[4]).decode(),
                                fields[5] == "1",
                                *[
                                    bytes.fromhex(field).decode()
                                    for field in fields[6:]
                                    if field != "-"
                                ],
                            ]
                        counts["link" if actual is not None else "rejected"] += 1
                    if actual != expected:
                        failures.append(
                            {
                                "mode": mode,
                                "data": value,
                                "file_enabled": enabled,
                                "restricted_schemes": restricted,
                                "stash": stash,
                                "expected": expected,
                                "actual": actual,
                            }
                        )
                if len(failures) > 100:
                    break
    sources = [
        Path(__file__).resolve(),
        root / "rust-backend/src/markdown_links.rs",
        root / "rust-backend/src/markdown_link_syntax.rs",
        root / "rust-backend/examples/links.rs",
        root / "zerver/lib/markdown/__init__.py",
        args.probe.resolve(),
        Path(inspect.getfile(LinkInlineProcessor)),
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
