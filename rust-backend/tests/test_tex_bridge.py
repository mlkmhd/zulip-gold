# ruff: noqa: INP001
import errno
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(
    sys.platform == "linux" and shutil.which("node"), "Linux and Node.js are required"
)
class TexBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.extension = import_module("_zulip_backend")
        directory = tempfile.TemporaryDirectory(prefix="zulip katex ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.script = self.root / "cli '$;.js"

    def run_script(
        self, source: str, content: bytes = b"", inline: bool = True
    ) -> tuple[bool, bytes]:
        self.script.write_text(source)
        return self.extension.run_katex_cli(str(self.script), content, inline)

    def test_binary_input_and_display_argument(self) -> None:
        source = """
            const fs = require('fs');
            process.stdout.write(fs.readFileSync(0));
            process.stdout.write(JSON.stringify(process.argv.slice(2)));
        """
        content = bytes(range(256)) * 1000
        for inline, arguments in [(True, b"[]"), (False, b'["--display-mode"]')]:
            with self.subTest(inline=inline):
                self.assertEqual(
                    self.run_script(source, content, inline), (True, content + arguments)
                )

    def test_nonzero_and_signal_exits_preserve_raw_output(self) -> None:
        for ending in ["process.exit(7)", "process.kill(process.pid, 'SIGTERM')"]:
            with self.subTest(ending=ending):
                success, stdout = self.run_script(
                    "require('fs').writeSync(1, Buffer.from([255, 13, 10]));" + ending
                )
                self.assertFalse(success)
                self.assertEqual(stdout, b"\xff\r\n")

    def test_ignored_file_size_signal_is_restored_in_child(self) -> None:
        import signal

        node = self.root / "node"
        node.write_text(f"#!/bin/sh\nulimit -c 0\nkill -{signal.SIGXFSZ.value} $$\nprintf alive\n")
        node.chmod(0o700)
        previous = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        try:
            with patch.dict(os.environ, PATH=str(self.root)):
                expected = subprocess.run(
                    ["node", str(self.script)], capture_output=True, check=False
                )
                self.assertEqual(expected.returncode, -signal.SIGXFSZ)
                self.assertEqual(
                    self.extension.run_katex_cli(str(self.script), b"", True), (False, b"")
                )
        finally:
            signal.signal(signal.SIGXFSZ, previous)

    def test_large_output_before_reading_input(self) -> None:
        content = b"input" * 200_000
        success, stdout = self.run_script(
            """
                const fs = require('fs');
                fs.writeFileSync(1, Buffer.alloc(1024 * 1024, 120));
                fs.writeFileSync(1, fs.readFileSync(0));
            """,
            content,
        )
        self.assertTrue(success)
        self.assertEqual(stdout, b"x" * (1024 * 1024) + content)

    def test_broken_pipe_does_not_override_exit_status(self) -> None:
        for status in [0, 1]:
            with self.subTest(status=status):
                self.assertEqual(
                    self.run_script(f"process.exit({status})", b"x" * (1024 * 1024)),
                    (status == 0, b""),
                )

    def test_spawn_errors_preserve_errno_and_filename(self) -> None:
        for mode, exception, code in [
            (None, FileNotFoundError, errno.ENOENT),
            (0o600, PermissionError, errno.EACCES),
        ]:
            with self.subTest(mode=mode):
                if mode is not None:
                    node = self.root / "node"
                    node.write_text("#!/bin/sh\nexit 0\n")
                    node.chmod(mode)
                with (
                    patch.dict(os.environ, PATH=str(self.root)),
                    self.assertRaises(exception) as raised,
                ):
                    self.extension.run_katex_cli(str(self.script), b"", True)
                self.assertEqual(raised.exception.errno, code)
                self.assertEqual(raised.exception.filename, "node")

    def test_nul_path_is_rejected_before_spawn(self) -> None:
        with self.assertRaisesRegex(ValueError, "embedded null byte"):
            self.extension.run_katex_cli("bad\0path", b"", True)

    def test_inheritable_application_descriptors_are_closed(self) -> None:
        import fcntl

        with tempfile.TemporaryFile() as secret:
            secret.write(b"private data")
            secret.seek(0)
            descriptor = fcntl.fcntl(secret.fileno(), fcntl.F_DUPFD, 200)
            self.addCleanup(os.close, descriptor)
            os.set_inheritable(descriptor, True)
            success, stdout = self.run_script(
                f"""
                    try {{
                        require('fs').readFileSync({descriptor});
                        process.exit(7);
                    }} catch (error) {{
                        if (error.code !== 'EBADF') process.exit(8);
                    }}
                """
            )
            self.assertTrue(success)
            self.assertEqual(stdout, b"")
            self.assertTrue(os.get_inheritable(descriptor))
            self.assertEqual(secret.read(), b"private data")

    def test_python_thread_can_release_a_waiting_child(self) -> None:
        started = self.root / "started"
        released = self.root / "released"
        source = """
            const fs = require('fs');
            const path = require('path');
            const root = path.dirname(process.argv[1]);
            fs.writeFileSync(path.join(root, 'started'), '');
            const deadline = Date.now() + 10000;
            while (!fs.existsSync(path.join(root, 'released'))) {
                if (Date.now() > deadline) process.exit(7);
                Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
            }
            process.stdout.write('released');
        """

        def release() -> None:
            deadline = time.monotonic() + 15
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(0.005)
            if started.exists():
                released.touch()

        worker = threading.Thread(target=release)
        worker.start()
        try:
            self.assertEqual(self.run_script(source), (True, b"released"))
        finally:
            worker.join(timeout=16)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
