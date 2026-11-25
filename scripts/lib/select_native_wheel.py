"""Verify a native wheel staged in an already trusted Zulip deployment."""

import argparse
import hashlib
import json
import platform
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

BUILD_ID = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
WHEEL = re.compile(
    r"zulip_backend-[0-9]+(?:\.[0-9]+)+-cp310-abi3-manylinux_2_34_(x86_64|aarch64)\.whl"
)


RELEASE_WHEEL_DIR = "rust-wheels"
GIT_WHEEL_STORE = Path("/srv/zulip-rust-wheels")


def supported_wheel_platform(distro: dict[str, str], machine: str) -> bool:
    return (
        distro.get("ID") == "ubuntu" and distro.get("VERSION_ID") == "24.04" and machine == "x86_64"
    )


def git_checkout_build_id(root: Path, git_preexec: Callable[[], None] | None) -> str:
    checkout, build_id = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel", "HEAD"],
        text=True,
        preexec_fn=git_preexec,
    ).splitlines()
    if Path(checkout).resolve() != root:
        raise ValueError("Staged Rust wheel must match the invoking checkout")
    if not BUILD_ID.fullmatch(build_id):
        raise ValueError("Invalid Git build ID")
    return build_id


def _require_trusted_path(path: Path, expected_type: int, owner_uid: int) -> None:
    info = path.lstat()
    if (
        info.st_uid != owner_uid
        or stat.S_IFMT(info.st_mode) != expected_type
        or (info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    ):
        raise ValueError(f"Untrusted native wheel store entry: {path}")


def find_git_wheel_dir(
    root: Path,
    store: Path,
    git_preexec: Callable[[], None] | None,
    *,
    owner_uid: int = 0,
) -> Path | None:
    if not store.exists() and not store.is_symlink():
        return None
    # Only the administrator may select binaries for a root-run upgrade.
    _require_trusted_path(store.parent, stat.S_IFDIR, owner_uid)
    _require_trusted_path(store, stat.S_IFDIR, owner_uid)
    wheel_dir = store / git_checkout_build_id(root, git_preexec)
    if not wheel_dir.exists() and not wheel_dir.is_symlink():
        return None
    _require_trusted_path(wheel_dir, stat.S_IFDIR, owner_uid)
    for entry in wheel_dir.iterdir():
        _require_trusted_path(entry, stat.S_IFREG, owner_uid)
    return wheel_dir


def select_wheel(
    root: Path,
    wheel_dir: Path,
    machine: str,
    *,
    git_preexec: Callable[[], None] | None = None,
) -> tuple[Path, str]:
    manifest_path = wheel_dir / "manifest.json"
    if wheel_dir.is_symlink() or manifest_path.is_symlink():
        raise ValueError("Invalid staged Rust wheel directory")
    manifest = json.loads(manifest_path.read_text())
    if (root / ".git").exists():
        build_id = git_checkout_build_id(root, git_preexec)
        if (root / "build_id").exists() and (root / "build_id").read_text().strip() != build_id:
            raise ValueError("Git HEAD and release build ID disagree")
    else:
        build_id = (root / "build_id").read_text().strip()
    if not BUILD_ID.fullmatch(build_id) or manifest["build_id"] != build_id:
        raise ValueError("Staged Rust wheel belongs to a different release commit")
    entries = {}
    for entry in manifest["wheels"]:
        match = WHEEL.fullmatch(entry["file"])
        if not match or not SHA256.fullmatch(entry["sha256"]):
            raise ValueError("Invalid staged Rust wheel metadata")
        arch = match.group(1)
        if arch in entries:
            raise ValueError("Duplicate staged Rust wheel for " + arch)
        entries[arch] = entry
    if machine not in entries:
        raise ValueError(f"No staged wheel for {machine}")
    entry = entries[machine]
    path = wheel_dir / entry["file"]
    if not path.is_file() or path.is_symlink():
        raise ValueError("Missing or invalid staged Rust wheel")
    digest = hashlib.sha256()
    with path.open("rb") as wheel:
        for chunk in iter(lambda: wheel.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != entry["sha256"]:
        raise ValueError("Staged Rust wheel checksum mismatch")
    return path, entry["sha256"]


def stage_release_wheel(root: Path, source: Path, machine: str) -> None:
    path, sha256 = select_wheel(root, source, machine)
    dest = root / RELEASE_WHEEL_DIR
    dest.mkdir()
    shutil.copyfile(path, dest / path.name)
    manifest = {
        "build_id": (root / "build_id").read_text().strip(),
        "wheels": [{"file": path.name, "sha256": sha256}],
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    select_wheel(root, dest, machine)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage a verified native wheel in a release tree")
    parser.add_argument("release_root", type=Path)
    parser.add_argument("wheel_dir", type=Path)
    args = parser.parse_args()
    machine = platform.machine()
    if not supported_wheel_platform(platform.freedesktop_os_release(), machine):
        parser.error("staging Rust wheels requires Ubuntu 24.04 x86-64")
    stage_release_wheel(args.release_root.resolve(), args.wheel_dir.absolute(), machine)


if __name__ == "__main__":
    main()
