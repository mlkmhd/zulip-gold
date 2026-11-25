#!/usr/bin/env python3
"""Build a portable x86-64 Rust wheel from the exact HEAD of a local checkout."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

TARGET = "x86_64-unknown-linux-gnu"
COMPATIBILITY = "manylinux_2_34"
TAG = f"cp310-abi3-{COMPATIBILITY}_x86_64"
MATURIN_VERSION = "maturin 1.9.4"
ZIG_VERSION = "0.14.1"


def offline_env() -> dict[str, str]:
    return {**os.environ, "CARGO_NET_OFFLINE": "true", "RUSTUP_AUTO_INSTALL": "0"}


def installed_rust_tool(tool: str, repo: Path, env: dict[str, str]) -> str:
    program = shutil.which(tool)
    if program is None:
        raise FileNotFoundError(tool)
    if Path(program).resolve().name != "rustup":
        return program
    return subprocess.check_output(
        [str(Path(program).resolve()), "which", tool],
        cwd=repo,
        env=env,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=10,
    ).strip()


def unavailable_reason(repo: Path, maturin: str, env: dict[str, str]) -> str | None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        return "automatic native wheels target Ubuntu 24.04 x86-64"
    try:
        distro = platform.freedesktop_os_release()
    except OSError:
        return "cannot determine the builder distribution"
    if distro.get("ID") != "ubuntu" or distro.get("VERSION_ID") != "24.04":
        return "automatic native wheels target Ubuntu 24.04 x86-64"

    # The recipe does not provision a toolchain or populate the crate cache.
    for command, expected in (
        ([maturin, "--version"], MATURIN_VERSION),
        (["zig", "version"], ZIG_VERSION),
    ):
        try:
            found = subprocess.check_output(
                command, cwd=repo, env=env, stderr=subprocess.DEVNULL, text=True, timeout=10
            ).strip()
        except (OSError, subprocess.SubprocessError):
            return f"{expected} is not available"
        if found != expected:
            return f"{expected} is required (found {found})"

    manifest = repo / "rust-backend/Cargo.toml"
    try:
        cargo = installed_rust_tool("cargo", repo, env)
        rustc = installed_rust_tool("rustc", repo, env)
        env["PATH"] = os.pathsep.join(
            [str(Path(cargo).parent), str(Path(rustc).parent), env.get("PATH", "")]
        )
        version_text = subprocess.check_output(
            [rustc, "--version"],
            cwd=repo,
            env=env,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()
        metadata = subprocess.check_output(
            [
                cargo,
                "metadata",
                "--locked",
                "--offline",
                "--format-version",
                "1",
                "--filter-platform",
                TARGET,
                "--manifest-path",
                str(manifest),
            ],
            cwd=repo,
            env=env,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return "the offline Cargo toolchain or locked dependencies are not available"

    (package,) = [
        package
        for package in json.loads(metadata)["packages"]
        if Path(package["manifest_path"]) == manifest
    ]
    required_version = package["rust_version"]
    minimum = tuple(map(int, required_version.split(".")))
    version = re.match(r"rustc ([0-9]+)[.]([0-9]+)[.]([0-9]+)(?: |$)", version_text)
    if version is None or tuple(map(int, version.groups()[: len(minimum)])) < minimum:
        return f"Rust {required_version} or newer is required"
    return None


def build(repo: Path, output: Path, maturin: str, env: dict[str, str]) -> None:
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
    if subprocess.check_output([maturin, "--version"], text=True).strip() != MATURIN_VERSION:
        raise SystemExit("Maturin 1.9.4 is required.")
    if subprocess.check_output(["zig", "version"], text=True).strip() != ZIG_VERSION:
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
            env=env,
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
    parser.add_argument(
        "--if-ready",
        action="store_true",
        help="Skip without producing output when the scoped offline toolchain is unavailable",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error(f"Output already exists: {output}")
    env = offline_env()
    if args.if_ready and (reason := unavailable_reason(repo, args.maturin, env)) is not None:
        print(f"Skipping bundled Rust wheel: {reason}", file=sys.stderr)
        return
    build(repo, output, args.maturin, env)


if __name__ == "__main__":
    main()
