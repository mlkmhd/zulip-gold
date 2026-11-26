import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from zerver.lib import tex


@override_settings(KATEX_SERVER=False, PRODUCTION=False)
class KatexCliDispatchRustTest(SimpleTestCase):
    def test_installed_cli_default_matches_python_and_handles_nonzero_exit(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("Node.js required")
        backend = tex._rust_development_cli
        if backend is None:
            self.skipTest("No development KaTeX CLI ABI")

        with (
            TemporaryDirectory(prefix="zulip-katex-dispatch-") as root,
            self.settings(DEPLOY_ROOT=root),
        ):
            cli = Path(root) / "node_modules/katex/cli.js"
            cli.parent.mkdir(parents=True)
            cli.write_text(
                """
                const fs = require('fs');
                const input = fs.readFileSync(0, 'utf8');
                if (input === 'invalid') process.exit(1);
                const mode = process.argv.includes('--display-mode') ? 'display' : 'inline';
                process.stdout.write('  ' + mode + '\\r\\n' + input + '\\r\\n  ');
                """
            )
            for formula, inline in (("یادداشت", True), ("α + β", False), ("invalid", True)):
                with self.subTest(formula=formula, inline=inline):
                    with patch.object(tex, "_rust_development_cli", None):
                        expected = tex.render_tex(formula, is_inline=inline)
                    with patch.object(backend, "run_katex_cli", wraps=backend.run_katex_cli) as run:
                        actual = tex.render_tex(formula, is_inline=inline)
                    run.assert_called_once()
                    self.assertEqual(actual, expected)

    def test_unencodable_formula_delegates_before_any_native_spawn(self) -> None:
        backend = tex._rust_development_cli
        if backend is None:
            self.skipTest("No development KaTeX CLI ABI")
        with (
            TemporaryDirectory(prefix="zulip-katex-dispatch-") as root,
            self.settings(DEPLOY_ROOT=root),
        ):
            cli = Path(root) / "node_modules/katex/cli.js"
            cli.parent.mkdir(parents=True)
            cli.touch()
            # A failed executable lookup must still precede the input encoding error,
            # as with subprocess. The installed interpreter is deliberately absent
            # from PATH only within this test.
            with (
                patch.dict(os.environ, {"PATH": root}),
                patch.object(backend, "run_katex_cli", wraps=backend.run_katex_cli) as run,
                self.assertRaises(FileNotFoundError),
            ):
                tex.render_tex("\ud800")
            run.assert_not_called()
