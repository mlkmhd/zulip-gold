# ruff: noqa: INP001
import argparse
import ast
import inspect
import itertools
import json
import random
import re
import textwrap
import unicodedata
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import django


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare native code headers with Python")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--random-cases", type=int, default=20000)
    parser.add_argument("--unicode", action="store_true")
    parser.add_argument("--mutant-remove-header", action="store_true")
    args = parser.parse_args()
    django.setup()
    from zerver.lib.markdown import fenced_code

    extension = import_module("_zulip_backend")
    tree = ast.parse(textwrap.dedent(inspect.getsource(fenced_code.CodeHilite._parseHeader)))
    patterns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
        and node.func.attr == "compile"
    ]
    assert len(patterns) == 1
    pattern = re.compile(ast.literal_eval(patterns[0].args[0]), re.VERBOSE)
    rng = random.Random(962848)
    checked = matched = states = exceptions = native_calls = delegated = 0
    differences = []

    def check(source: str, *, state: bool = False) -> None:
        nonlocal checked, matched, states, exceptions, native_calls, delegated
        match = pattern.search(source.split("\n", maxsplit=1)[0])
        header = (
            None if match is None else (match["lang"], bool(match["shebang"]), match["hl_lines"])
        )
        expected = (source.strip("\n"), header)
        result = extension.parse_fenced_code_header(source, unicodedata.unidata_version)
        try:
            source.encode("utf-8")
        except UnicodeEncodeError:
            assert result is None
        else:
            assert result is not None, repr(source)
        native_calls += result is not None
        delegated += result is None
        actual = expected if result is None else result
        if args.mutant_remove_header and header is not None:
            actual = (source.partition("\n")[2], header)
        checked += 1
        matched += header is not None
        if actual != expected:
            differences.append(
                {"source": repr(source), "expected": repr(expected), "actual": repr(actual)}
            )
        if state:
            expected_state: object = None
            linenos = rng.choice([None, False, True])
            missing = rng.randrange(20) == 0
            for native in [None, extension]:
                highlighter = fenced_code.CodeHilite(
                    source, lang="INITIAL", linenums=linenos, hl_lines=[9]
                )
                if missing:
                    del highlighter.options["linenos"]
                with patch.object(fenced_code, "_rust_code_header", native):
                    try:
                        highlighter._parseHeader()
                        outcome: tuple[object, ...] = ("returned",)
                    except Exception as error:
                        outcome = ("raised", type(error).__name__, error.args)
                snapshot = (outcome, highlighter.src, highlighter.lang, highlighter.options)
                if native is None:
                    expected_state = snapshot
                elif snapshot != expected_state:
                    differences.append(
                        {
                            "source": repr(source),
                            "expected_state": repr(expected_state),
                            "actual_state": repr(snapshot),
                        }
                    )
            states += 1
            exceptions += outcome[0] == "raised"

    for prefix in ["", "#!", "::", ":::", "#!/usr/bin/", "#!/usr/bin/env ", ":: /", "\n#!", " #!"]:
        for lang in ["", "PYTHON", "c++", "c#", "İΣ", "فارسی", "²", "a\u0301", "📝", "\ud800"]:
            for suffix in [
                "",
                " hl_lines='1 2'",
                ' hl_lines="۱ +٢ -3 1_0"',
                " hl_lines='1\"2'",
                " hl_lines='1\r2'",
                " hl_lines='1\n2'",
                " hl_lines='" + "9" * 5000 + "'",
                "\n<x>&\n\n",
            ]:
                check(prefix + lang + suffix, state=True)
    alphabet = "a/#:! \n"
    for length in range(7):
        for parts in itertools.product(alphabet, repeat=length):
            check("".join(parts))
        if len(differences) >= 10:
            break
    for _ in range(args.random_cases):
        prefix = rng.choice(["#!", "::", "::: ", "#!/usr/bin/", "", "\n"])
        body = "".join(
            rng.choices("aA0/#.+-:! \t\r\n'\"_²İΣفارسی😀\u001c\u200b", k=rng.randrange(100))
        )
        suffix = rng.choice(["", " hl_lines='1 2'", ' hl_lines="۱ ٢"', " hl_lines='1 nope 3'"])
        check(prefix + body + suffix, state=True)
        if len(differences) >= 10:
            break
    if args.unicode and not differences:
        for value in range(0x110000):
            if 0xD800 <= value <= 0xDFFF:
                continue
            char = chr(value)
            for source in [f":::{char}", f"#!/a{char}/X", f":::X{char}hl_lines='1'"]:
                check(source)
            if len(differences) >= 10:
                break
    report = {
        "seed": 962848,
        "checked": checked,
        "headers": matched,
        "states": states,
        "exceptions": exceptions,
        "native_calls": native_calls,
        "delegated": delegated,
        "differences": differences[:10],
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=True))
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
