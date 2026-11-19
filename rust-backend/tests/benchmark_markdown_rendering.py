# ruff: noqa: INP001
import argparse
import os
import platform
import statistics
import sys
import timeit
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from xml.etree.ElementTree import HTML_EMPTY, Element, SubElement

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def measure(operation: Callable[[], object], iterations: int, repeats: int) -> float:
    operation()
    return (
        statistics.median(timeit.repeat(operation, number=iterations, repeat=repeats)) / iterations
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare optional Markdown bridges with Python.")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.iterations < 1 or args.repeats < 1:
        parser.error("iterations and repeats must be positive")
    if any(name.startswith("ZULIP_RUST_") and value == "1" for name, value in os.environ.items()):
        parser.error("unset Rust feature flags to isolate the measured bridges")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")

    import django
    import orjson
    from markdown.serializers import to_html_string

    django.setup()

    from zerver.lib.markdown import (
        MessageRenderingResult,
        do_convert,
        inline_stash,
        postprocessing,
        prettify,
        serialization,
        unescape,
    )
    from zerver.lib.partial import partial

    def render(content: str) -> MessageRenderingResult:
        return do_convert(content, no_previews=True)

    native = import_module("_zulip_backend")
    bridges = {
        "serialization": (serialization, "_rust_serialization"),
        "prettify": (prettify, "_rust_prettify"),
        "inline_stash": (inline_stash, "_rust_inline_stash"),
        "inline_patterns": (inline_stash, "_rust_inline_patterns"),
        "postprocessing": (postprocessing, "_rust_postprocessing"),
        "unescape": (unescape, "_rust_unescape"),
    }
    samples = {
        "short": "سلام **دنیا**\nمتن `code` و *تأکید*.",
        "rich": (
            "## عنوان **پررنگ**\n\n"
            "> نقل‌قول با ***~~قالب‌بندی~~***\n\n"
            "* مورد اول\n* مورد دوم با `inline <&>`\n\n"
            "|عنوان|کد|\n|---|---:|\n|متن|`a|b`|\n\n"
            "```python\nprint('فارسی')\n```\n\n&copy; &bogus;"
        ),
    }
    samples["long"] = (samples["rich"] + "\n\n") * 20
    results = []
    for name, content in samples.items():
        expected = render(content)
        baseline = 0.0
        iterations = max(1, args.iterations // 20) if name == "long" else args.iterations
        for mode in ["python", *bridges, "combined"]:
            for bridge, (module, attribute) in bridges.items():
                setattr(module, attribute, native if mode in (bridge, "combined") else None)
            assert render(content) == expected
            elapsed = measure(partial(render, content), iterations, args.repeats)
            if mode == "python":
                baseline = elapsed
            results.append(
                {
                    "sample": name,
                    "mode": mode,
                    "microseconds": elapsed * 1e6,
                    "ratio": elapsed / baseline,
                }
            )
        for module, attribute in bridges.values():
            setattr(module, attribute, None)

    for count in [2, 100]:
        tree = Element("div")
        for _ in range(count):
            paragraph = SubElement(tree, "p", {"title": 'فارسی <&> "'})
            paragraph.text = "متن &copy; &bogus; "
            SubElement(paragraph, "strong").text = "تأکید"
        snapshot = [
            (0, node.tag, node.text, node.tail, list(node.items()), len(node))
            for node in tree.iter()
        ]
        serialization._rust_serialization = native
        expected_html = to_html_string(tree)
        assert serialization.to_html_string(tree) == expected_html
        assert native.serialize_markdown_html(snapshot, list(HTML_EMPTY)) == expected_html
        operations = {
            "python": partial(to_html_string, tree),
            "bridge": partial(serialization.to_html_string, tree),
            "prebuilt_snapshot": partial(
                native.serialize_markdown_html, snapshot, list(HTML_EMPTY)
            ),
        }
        baseline = 0.0
        for mode, operation in operations.items():
            elapsed = measure(operation, args.iterations, args.repeats)
            if mode == "python":
                baseline = elapsed
            results.append(
                {
                    "sample": f"serialize_{count}",
                    "mode": mode,
                    "microseconds": elapsed * 1e6,
                    "ratio": elapsed / baseline,
                }
            )
    print(
        orjson.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "extension": native.__file__,
                "iterations": args.iterations,
                "repeats": args.repeats,
                "results": results,
            },
            option=orjson.OPT_INDENT_2,
        ).decode()
    )


if __name__ == "__main__":
    main()
