"""Verify a native wheel staged in an already trusted Zulip deployment."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

BUILD_ID = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
WHEEL = re.compile(
    r"zulip_backend-[0-9]+(?:\.[0-9]+)+-cp310-abi3-manylinux_2_34_(x86_64|aarch64)\.whl"
)


def select_wheel(root: Path, wheel_dir: Path, machine: str) -> tuple[Path, str]:
    manifest_path = wheel_dir / "manifest.json"
    if wheel_dir.is_symlink() or manifest_path.is_symlink():
        raise ValueError("Invalid staged Rust wheel directory")
    manifest = json.loads(manifest_path.read_text())
    if (root / ".git").exists():
        checkout, build_id = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel", "HEAD"], text=True
        ).splitlines()
        if Path(checkout).resolve() != root:
            raise ValueError("Staged Rust wheel must match the invoking checkout")
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
