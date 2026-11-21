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
    storage.static_path = lambda name: name  # type: ignore[attr-defined]
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
