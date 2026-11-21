# ruff: noqa: INP001
import argparse
import itertools
import json
import random
from importlib import import_module
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare native header values with Python")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mutant-drop-sign", action="store_true")
    args = parser.parse_args()
    from markdown.extensions.codehilite import parse_hl_lines

    extension = import_module("_zulip_backend")
    checked = accepted = delegated = 0
    differences = []

    def check(expression: str | None) -> None:
        nonlocal checked, accepted, delegated
        expected = parse_hl_lines(expression or "")
        result = extension.parse_fenced_highlight_lines(expression)
        checked += 1
        if result is None:
            delegated += 1
            return
        accepted += 1
        if args.mutant_drop_sign:
            result = [abs(value) for value in result]
        if result != expected and len(differences) < 10:
            differences.append(
                {"expression": repr(expression), "expected": expected, "actual": result}
            )

    check(None)
    for size in range(6):
        for token in itertools.product("01_+- x\t\x1c", repeat=size):
            check("".join(token))
    rng = random.Random(962852)
    for _ in range(20000):
        bits = rng.choice([31, 63, 80])
        values = [rng.randrange(-(2**bits), 2**bits) for _ in range(rng.randrange(6))]
        check(" ".join(str(value) for value in values))
        check(" ".join(f"{value:_}" for value in values))
    for digits in [18, 19, 20, 639, 640, 641, 4300, 4301]:
        for digit in "019":
            check(digit * digits)
    for scalar in range(0x110000):
        character = chr(scalar)
        language = f"PY{character}THON"
        result = extension.normalize_fenced_code_language(language)
        if result is not None:
            assert result == language.lower(), repr(language)
        check(f"1{character}-2")
    for expression in ["x" * (8 * 1024 * 1024), "x" * (8 * 1024 * 1024 + 1)]:
        check(expression)
    args.report.write_text(
        json.dumps(
            {
                "seed": 962852,
                "checked": checked,
                "accepted": accepted,
                "delegated": delegated,
                "differences": differences,
            },
            indent=2,
        )
        + "\n"
    )
    assert not differences, differences


if __name__ == "__main__":
    main()
