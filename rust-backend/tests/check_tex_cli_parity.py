# ruff: noqa: INP001
import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--katex", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=200)
    parser.add_argument("--seed", type=int, default=962841)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    extension = import_module("_zulip_backend")
    rng = random.Random(args.seed)
    cases = [
        "",
        "x",
        r"\frac{1}{2}",
        r"\int_a^b f(t)\, dt = F(b) - F(a)",
        r"\begin{matrix}a&b\\c&d\end{matrix}",
        r"\def\foo{x}\foo",
        r"\href{javascript:alert(1)}{click}",
        r"\htmlClass{test}{x}",
        r"\invalidcommand",
        r"\frac{",
        "a\0b",
        "سلام",
        "α + β = γ",
        "x\r\ny\rz",
        "<&>'\"",
        r"\text{<script>alert(1)</script>}",
        r"\text{سلام}",
    ]
    atoms = [
        "x",
        "y",
        "0",
        "1",
        "é",
        "α",
        "سلام",
        "😊",
        "{",
        "}",
        "_",
        "^",
        "&",
        "$",
        "+",
        "-",
        "=",
        " ",
        "\n",
        "\r",
        "\0",
        r"\frac",
        r"\sqrt",
        r"\text",
        r"\sum",
        r"\alpha",
        r"\unknown",
        r"\begin{aligned}",
        r"\end{aligned}",
    ]
    cases.extend("".join(rng.choices(atoms, k=rng.randrange(1, 30))) for _ in range(args.cases))
    checked = valid = invalid = 0
    differences: list[dict[str, object]] = []
    for content in cases:
        payload = content.encode()
        for inline in [True, False]:
            command = ["node", str(args.katex)]
            if not inline:
                command.append("--display-mode")
            expected = subprocess.run(
                command,
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            success, stdout = extension.run_katex_cli(str(args.katex), payload, inline)
            checked += 1
            valid += expected.returncode == 0
            invalid += expected.returncode != 0
            if (success, stdout) != (expected.returncode == 0, expected.stdout):
                differences.append({"content": content, "inline": inline})
            if checked % 100 == 0:
                print(f"checked={checked} differences={len(differences)}", flush=True)
    root = Path(__file__).resolve().parents[2]
    sources = [
        "rust-backend/src/markdown_tex.rs",
        "rust-backend/src/python_tex.rs",
        "rust-backend/src/lib.rs",
        "rust-backend/src/python.rs",
        "rust-backend/Cargo.toml",
        "rust-backend/Cargo.lock",
        "rust-backend/tests/check_tex_cli_parity.py",
        "rust-backend/tests/test_tex_bridge.py",
        "zerver/lib/tex.py",
        "zerver/tests/test_tex_rust.py",
    ]
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources}
    hashes[str(args.katex.resolve())] = hashlib.sha256(args.katex.read_bytes()).hexdigest()
    for name in ["package.json", "dist/katex.js"]:
        path = args.katex.resolve().parent / name
        if path.is_file():
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    extension_spec = find_spec("_zulip_backend._zulip_backend")
    assert extension_spec is not None and extension_spec.origin is not None
    extension_path = Path(extension_spec.origin)
    hashes[str(extension_path)] = hashlib.sha256(extension_path.read_bytes()).hexdigest()
    node = shutil.which("node")
    assert node is not None
    hashes[str(Path(node).resolve())] = hashlib.sha256(Path(node).read_bytes()).hexdigest()
    report = {
        "seed": args.seed,
        "random_cases": args.cases,
        "checked": checked,
        "valid": valid,
        "invalid": invalid,
        "differences": differences,
        "python": sys.version,
        "hashes": hashes,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "hashes"}))
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
