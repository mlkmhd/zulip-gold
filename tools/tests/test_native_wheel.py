import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from scripts.lib.select_native_wheel import (
    RELEASE_WHEEL_DIR,
    find_git_wheel_dir,
    select_wheel,
    stage_git_wheel,
    stage_release_wheel,
)

BUILD_ID = "a" * 40
WHEEL_NAME = "zulip_backend-0.1.0-cp310-abi3-manylinux_2_34_x86_64.whl"
WHEEL_BYTES = b"synthetic wheel"
DIGEST = hashlib.sha256(WHEEL_BYTES).hexdigest()


class ReleaseWheelTest(TestCase):
    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "release"
        self.root.mkdir()
        (self.root / "build_id").write_text(BUILD_ID + "\n")
        self.source = Path(temp.name) / "source"
        self.source.mkdir()
        (self.source / WHEEL_NAME).write_bytes(WHEEL_BYTES)
        (self.source / "manifest.json").write_text(
            json.dumps({"build_id": BUILD_ID, "wheels": [{"file": WHEEL_NAME, "sha256": DIGEST}]})
        )
        self.bundled = self.root / RELEASE_WHEEL_DIR

    def test_copies_only_verified_files(self) -> None:
        (self.source / "unrelated.txt").touch()
        stage_release_wheel(self.root, self.source, "x86_64")
        self.assertEqual(
            {path.name for path in self.bundled.iterdir()}, {WHEEL_NAME, "manifest.json"}
        )
        self.assertEqual(
            select_wheel(self.root, self.bundled, "x86_64"),
            (self.bundled / WHEEL_NAME, DIGEST),
        )

    def test_rejects_a_wheel_from_another_commit(self) -> None:
        (self.root / "build_id").write_text("b" * 40)
        with self.assertRaisesRegex(ValueError, "different release commit"):
            stage_release_wheel(self.root, self.source, "x86_64")
        self.assertFalse(self.bundled.exists())

    def test_rejects_modified_bundle(self) -> None:
        stage_release_wheel(self.root, self.source, "x86_64")
        (self.bundled / WHEEL_NAME).write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            select_wheel(self.root, self.bundled, "x86_64")

    def test_does_not_replace_an_existing_bundle(self) -> None:
        self.bundled.mkdir()
        with self.assertRaises(FileExistsError):
            stage_release_wheel(self.root, self.source, "x86_64")
        self.assertEqual(list(self.bundled.iterdir()), [])

    def test_git_verification_uses_the_supplied_process_identity(self) -> None:
        (self.root / ".git").touch()
        (self.root / "build_id").unlink()

        def checkout_owner() -> None:
            return

        with mock.patch(
            "scripts.lib.select_native_wheel.subprocess.check_output",
            return_value=f"{self.root}\n{BUILD_ID}\n",
        ) as git:
            self.assertEqual(
                select_wheel(self.root, self.source, "x86_64", git_preexec=checkout_owner),
                (self.source / WHEEL_NAME, DIGEST),
            )
            git.assert_called_once_with(
                ["git", "-C", str(self.root), "rev-parse", "--show-toplevel", "HEAD"],
                text=True,
                preexec_fn=checkout_owner,
            )

    def test_tarball_rejects_a_build_and_prebuilt_wheel_together(self) -> None:
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [
                str(root / "tools/build-release-tarball"),
                "--build-rust-wheel",
                "--rust-wheel-dir",
                str(self.source),
                "test-wheel",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Usage:", result.stderr)

    def test_automatic_build_skips_without_touching_the_output(self) -> None:
        root = Path(__file__).resolve().parents[2]
        output = self.root / "built-wheels"
        result = subprocess.run(
            [
                sys.executable,
                str(root / "rust-backend/tools/build_portable_wheel.py"),
                "--if-ready",
                "--maturin",
                str(self.root / "missing-maturin"),
                "--output",
                str(output),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipping bundled Rust wheel:", result.stderr)
        self.assertFalse(output.exists())


class GitWheelStoreTest(TestCase):
    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "checkout"
        self.store = self.base / "zulip-rust-wheels"
        patcher = mock.patch(
            "scripts.lib.select_native_wheel.git_checkout_build_id", return_value=BUILD_ID
        )
        self.git_checkout_build_id = patcher.start()
        self.addCleanup(patcher.stop)

    def select(self, *, owner_uid: int | None = None) -> Path | None:
        return find_git_wheel_dir(
            self.root,
            self.store,
            None,
            owner_uid=owner_uid if owner_uid is not None else os.getuid(),
        )

    def stage(self, build_id: str = BUILD_ID) -> Path:
        wheel_dir = self.store / build_id
        self.store.mkdir(mode=0o755, exist_ok=True)
        wheel_dir.mkdir(mode=0o755)
        (wheel_dir / "manifest.json").write_text(
            json.dumps({"build_id": build_id, "wheels": [{"file": WHEEL_NAME, "sha256": DIGEST}]})
        )
        (wheel_dir / WHEEL_NAME).write_bytes(WHEEL_BYTES)
        for entry in wheel_dir.iterdir():
            entry.chmod(0o644)
        return wheel_dir

    def test_stage_publishes_only_the_verified_exact_commit(self) -> None:
        source = self.stage()
        incoming = self.base / "incoming"
        source.rename(incoming)
        (incoming / "ignore").touch()
        exact = stage_git_wheel(incoming, self.store, BUILD_ID, "x86_64")
        self.assertEqual(exact, self.store / BUILD_ID)
        self.assertEqual(self.select(), exact)
        self.assertEqual({file.name for file in exact.iterdir()}, {WHEEL_NAME, "manifest.json"})
        self.assertEqual((exact / WHEEL_NAME).read_bytes(), WHEEL_BYTES)
        self.assertEqual(list(self.store.iterdir()), [exact])
        with self.assertRaises(FileExistsError):
            stage_git_wheel(incoming, self.store, BUILD_ID, "x86_64")
        self.assertEqual((exact / WHEEL_NAME).read_bytes(), WHEEL_BYTES)

    def test_stage_rejects_corrupt_wheel_and_wrong_commit_before_publication(self) -> None:
        source = self.stage()
        incoming = self.base / "incoming"
        source.rename(incoming)
        with self.assertRaisesRegex(ValueError, "different release commit"):
            stage_git_wheel(incoming, self.store, "b" * 40, "x86_64")
        (incoming / WHEEL_NAME).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            stage_git_wheel(incoming, self.store, BUILD_ID, "x86_64")
        self.assertEqual(list(self.store.iterdir()), [])

    def test_stage_revalidates_the_copy_and_leaves_no_incomplete_entry(self) -> None:
        source = self.stage()
        incoming = self.base / "incoming"
        source.rename(incoming)

        def copy_corrupt_wheel(_source: Path, destination: Path) -> None:
            destination.write_bytes(b"corrupted during copy")

        with (
            mock.patch("scripts.lib.select_native_wheel.shutil.copyfile", copy_corrupt_wheel),
            self.assertRaisesRegex(ValueError, "checksum mismatch"),
        ):
            stage_git_wheel(incoming, self.store, BUILD_ID, "x86_64")
        self.assertEqual(list(self.store.iterdir()), [])

    def test_missing_store_does_not_read_git(self) -> None:
        self.assertIsNone(self.select())
        self.git_checkout_build_id.assert_not_called()

    def test_store_does_not_fall_back_to_another_commit(self) -> None:
        self.stage("b" * 40)
        self.assertIsNone(self.select())

    def test_finds_only_exact_commit_in_trusted_store(self) -> None:
        self.stage("b" * 40)
        exact = self.stage()
        self.assertEqual(self.select(), exact)
        self.git_checkout_build_id.assert_called_once_with(self.root, None)

    def test_rejects_wrong_owner(self) -> None:
        self.stage()
        with self.assertRaisesRegex(ValueError, "Untrusted native wheel store entry"):
            self.select(owner_uid=os.getuid() + 1)

    def test_rejects_writable_manifest(self) -> None:
        exact = self.stage()
        (exact / "manifest.json").chmod(0o664)
        with self.assertRaisesRegex(ValueError, "Untrusted native wheel store entry"):
            self.select()

    def test_rejects_symlinked_store(self) -> None:
        self.stage()
        alias = self.base / "actual"
        self.store.rename(alias)
        self.store.symlink_to(alias)
        with self.assertRaisesRegex(ValueError, "Untrusted native wheel store entry"):
            self.select()
