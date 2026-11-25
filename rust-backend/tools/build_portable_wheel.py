#!/usr/bin/env python3
"""Build a portable x86-64 Rust wheel from the exact HEAD of a local checkout."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

TARGET = "x86_64-unknown-linux-gnu"
COMPATIBILITY = "manylinux_2_34"
TAG = f"cp310-abi3-{COMPATIBILITY}_x86_64"


def build(repo: Path, output: Path, maturin: str) -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("This build recipe supports only Linux x86-64.")
    if output.exists() or output.is_symlink():
        raise SystemExit(f"Output already exists: {output}")

    if not output.parent.is_dir():
        raise SystemExit(f"Output parent directory does not exist: {output.parent}")
    executable = shutil.which(maturin)
    if executable is None:
        raise SystemExit(f"Maturin executable not found: {maturin}")
    maturin = str(Path(executable).resolve())

    source = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"], text=True
    ).strip()
    if subprocess.check_output([maturin, "--version"], text=True).strip() != "maturin 1.9.4":
        raise SystemExit("Maturin 1.9.4 is required.")
    if subprocess.check_output(["zig", "version"], text=True).strip() != "0.14.1":
        raise SystemExit("Zig 0.14.1 is required.")

    with TemporaryDirectory(prefix="zulip-native-wheel-") as tmp:
        work = Path(tmp)
        archive = work / "source.tar"
        subprocess.run(
            ["git", "-C", str(repo), "archive", "-o", str(archive), source, "rust-backend"],
            check=True,
        )
        subprocess.run(["tar", "-xf", str(archive), "-C", tmp], check=True)
        wheels = work / "wheels"
        subprocess.run(
            [
                maturin,
                "build",
                "--zig",
                "--compatibility",
                COMPATIBILITY,
                "--target",
                TARGET,
                "--release",
                "--locked",
                "--offline",
                "--manifest-path",
                "rust-backend/Cargo.toml",
                "--interpreter",
                sys.executable,
                "--out",
                str(wheels),
            ],
            cwd=work,
            env={**os.environ, "CARGO_NET_OFFLINE": "true"},
            check=True,
        )
        (wheel,) = wheels.glob(f"zulip_backend-*-{TAG}.whl")
        with ZipFile(wheel) as bundle:
            (metadata,) = [name for name in bundle.namelist() if name.endswith(".dist-info/WHEEL")]
            if f"Tag: {TAG}" not in bundle.read(metadata).decode().splitlines():
                raise ValueError("Unexpected native wheel tag")

        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        output.mkdir()
        shutil.copyfile(wheel, output / wheel.name)
        manifest = {"build_id": source, "wheels": [{"file": wheel.name, "sha256": digest}]}
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Built {wheel.name} from {source}: {digest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--maturin", default="maturin")
    args = parser.parse_args()
    build(args.repo.resolve(), args.output.absolute(), args.maturin)


if __name__ == "__main__":
    main()
