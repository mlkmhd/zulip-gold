import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from scripts.lib.select_native_wheel import RELEASE_WHEEL_DIR, select_wheel, stage_release_wheel

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
