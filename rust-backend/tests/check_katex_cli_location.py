# ruff: noqa: INP001

"""Check the live KaTeX path branch without a provisioned Django database."""

import importlib.util
import shutil
import sys
import unittest
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = import_module("_zulip_backend")


def load_tex() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_test_tex", ROOT / "zerver/lib/tex.py")
    assert spec is not None and spec.loader is not None
    tex = importlib.util.module_from_spec(spec)
    storage = ModuleType("zerver.lib.storage")
    storage.static_path = lambda name: name  # type: ignore[attr-defined] # minimal storage lookup stub
    with patch.dict(sys.modules, {"zerver.lib.storage": storage}):
        spec.loader.exec_module(tex)
    return tex


class CliLocationParity(unittest.TestCase):
    def test_actual_dispatch_preserves_path_hooks_and_failures(self) -> None:
        for production in [False, True]:
            for stage in ["success", "missing", "resolve-error", "isfile-error", "truth-error"]:
                with self.subTest(production=production, stage=stage):
                    baseline = self.exercise(production, stage, None)
                    self.assertEqual(self.exercise(production, stage, SimpleNamespace()), baseline)
                    with patch.object(
                        EXTENSION, "locate_katex_cli", wraps=EXTENSION.locate_katex_cli
                    ) as locate:
                        self.assertEqual(self.exercise(production, stage, EXTENSION), baseline)
                    locate.assert_called_once()

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for the CLI smoke test")
    def test_real_filesystem_and_cli(self) -> None:
        tex = load_tex()
        with TemporaryDirectory(prefix="katex cli فارسی ") as root:
            path = Path(root) / "node_modules/katex/cli.js"
            path.parent.mkdir(parents=True)
            path.write_text("process.stdin.pipe(process.stdout)", encoding="utf-8")
            for production in [False, True]:
                for backend in [None, EXTENSION]:
                    with (
                        self.subTest(production=production, native=backend is not None),
                        patch.object(tex, "_rust_markdown_tex", backend),
                        patch.object(
                            tex,
                            "settings",
                            SimpleNamespace(
                                KATEX_SERVER=False, PRODUCTION=production, DEPLOY_ROOT=root
                            ),
                        ),
                        patch.object(tex, "static_path", return_value=str(path)),
                    ):
                        self.assertEqual(tex.render_tex("  فارسی α  "), "فارسی α")

    def test_cli_guard_matches_python_short_circuit(self) -> None:
        for blocked in [None, "platform", "warn", "check_output", "Popen"]:
            with self.subTest(blocked=blocked):
                expected = self.exercise_guard(blocked, native=False)
                self.assertEqual(self.exercise_guard(blocked, native=True), expected)

    def exercise_guard(self, blocked: str | None, *, native: bool) -> tuple[object, list[str]]:
        tex = load_tex()
        seen: list[str] = []

        class Flags:
            @property
            def warn_default_encoding(self) -> bool:
                seen.append("warn")
                return blocked == "warn"

        class Runtime:
            flags = Flags()

            @property
            def platform(self) -> str:
                seen.append("platform")
                return "darwin" if blocked == "platform" else "linux"

        def fallback(*args: object, **kwargs: object) -> str:
            return "python"

        class Subprocess:
            DEVNULL = None

            @property
            def check_output(self) -> object:
                seen.append("check_output")
                return (lambda *args, **kwargs: "python") if blocked == "check_output" else fallback

            @property
            def Popen(self) -> object:
                seen.append("Popen")
                return object() if blocked == "Popen" else tex._subprocess_popen

        backend = SimpleNamespace(
            locate_katex_cli=lambda context: (True, "fake-katex.js"),
            run_katex_cli=lambda *args: (True, b"rust"),
            select_katex_encoding=lambda context: "utf-8",
        )
        if native:
            backend.katex_native_cli_allowed = EXTENSION.katex_native_cli_allowed
        with (
            patch.object(tex, "_rust_markdown_tex", backend),
            patch.object(tex, "settings", SimpleNamespace(KATEX_SERVER=False)),
            patch.object(tex, "sys", Runtime()),
            patch.object(tex, "_subprocess_check_output", fallback),
            patch.object(tex, "subprocess", Subprocess()),
        ):
            result = tex.render_tex("input")
        self.assertEqual(result, "rust" if blocked is None else "python")
        return result, seen

    def exercise(
        self, production: bool, stage: str, backend: object
    ) -> tuple[object, list[object]]:
        tex = load_tex()
        seen: list[object] = []
        path = Path("/katex launch path/فارسی")
        failure = RuntimeError(stage)

        class Settings:
            KATEX_SERVER = False

            @property
            def PRODUCTION(self) -> bool:
                seen.append("production")
                return production

            @property
            def DEPLOY_ROOT(self) -> str:
                seen.append("deploy-root")
                return "/development path"

        class Truth:
            def __bool__(self) -> bool:
                seen.append("isfile truth")
                if stage == "truth-error":
                    raise failure
                return stage != "missing"

        def resolve(*args: object) -> Path:
            seen.append(("resolve", args))
            if stage == "resolve-error":
                raise failure
            return path

        def isfile(value: object) -> Truth:
            self.assertIs(value, path)
            seen.append("isfile")
            if stage == "isfile-error":
                raise failure
            return Truth()

        def run(command: list[object], **kwargs: object) -> str:
            seen.append(("run", kwargs["input"], command[0], command[2:]))
            self.assertIs(command[1], path)
            return " result\n"

        with (
            patch.object(tex, "_rust_markdown_tex", backend),
            patch.object(tex, "settings", Settings()),
            patch.object(tex, "static_path", resolve),
            patch.object(
                tex, "os", SimpleNamespace(path=SimpleNamespace(join=resolve, isfile=isfile))
            ),
            patch.object(tex.subprocess, "check_output", run),
            patch.object(tex.logging, "error", lambda message: seen.append(("error", message))),
        ):
            try:
                result: object = tex.render_tex("α😀", is_inline=False)
            except RuntimeError as error:
                self.assertIs(error, failure)
                result = (type(error), error.args)
        return result, seen


if __name__ == "__main__":
    if not hasattr(EXTENSION, "locate_katex_cli"):
        raise RuntimeError("build the Python extension with locate_katex_cli first")
    unittest.main()
