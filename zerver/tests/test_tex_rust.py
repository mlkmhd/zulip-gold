import locale
import subprocess
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import override_settings

from zerver.lib import markdown, tex
from zerver.lib.test_classes import ZulipTestCase


@override_settings(KATEX_SERVER=False, PRODUCTION=False)
class TexRustTest(ZulipTestCase):
    def setUp(self) -> None:
        super().setUp()
        if sys.platform != "linux" or sys.flags.warn_default_encoding:
            raise SkipTest("Native KaTeX requires Linux without encoding warnings")
        try:
            self.extension = import_module("_zulip_backend")
        except ImportError:
            raise SkipTest("Rust extension is not installed") from None
        if not hasattr(self.extension, "run_katex_cli"):
            raise SkipTest("Rust extension does not support the KaTeX CLI")
        directory = tempfile.TemporaryDirectory(prefix="zulip katex ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.script = self.root / "node_modules/katex/cli.js"
        self.script.parent.mkdir(parents=True)
        self.deploy_root = settings.DEPLOY_ROOT
        self.settings_override = self.settings(DEPLOY_ROOT=str(self.root))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def compare(self, source: str, content: str, inline: bool = True) -> str | None:
        self.script.write_text(source)
        with patch.object(tex, "_rust_markdown_tex", None):
            expected = tex.render_tex(content, is_inline=inline)
        with patch.object(tex, "_rust_markdown_tex", self.extension):
            with patch.object(
                self.extension, "run_katex_cli", wraps=self.extension.run_katex_cli
            ) as run:
                actual = tex.render_tex(content, is_inline=inline)
            run.assert_called_once()
        self.assertEqual(actual, expected)
        return actual

    def test_unicode_input_newlines_and_display_mode(self) -> None:
        source = """
            const fs = require('fs');
            process.stdout.write('\u001c\u2003' + JSON.stringify([
                fs.readFileSync(0).toString('utf8'), process.argv.slice(2)
            ]) + '\\r\\ninside\\rend\\r\\n\u2003');
        """
        for inline in [True, False]:
            with self.subTest(inline=inline):
                result = self.compare(source, "سلام $\\alpha\0\r\n", inline)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertTrue(result.startswith('["سلام'))
                self.assertTrue(result.endswith("\ninside\nend"))
                self.assertEqual("--display-mode" in result, not inline)

    def test_empty_success_and_nonzero_failure(self) -> None:
        self.assertEqual(self.compare("process.exit(0)", ""), "")
        self.assertIsNone(self.compare("process.stdout.write('partial'); process.exit(7)", ""))

    def test_decode_failure_precedes_unsuccessful_exit(self) -> None:
        self.script.write_text("require('fs').writeSync(1, Buffer.from([255])); process.exit(7)")
        for backend in [None, self.extension]:
            with (
                self.subTest(native=backend is not None),
                patch.object(tex, "_rust_markdown_tex", backend),
                self.assertRaises(UnicodeDecodeError),
            ):
                tex.render_tex("input")

    def test_non_utf8_locale(self) -> None:
        if sys.flags.utf8_mode:
            self.skipTest("UTF-8 mode overrides the locale encoding")
        with patch.object(locale, "getencoding", return_value="latin-1"):
            self.assertEqual(self.compare("process.stdin.pipe(process.stdout)", "café\r\n"), "café")

    def test_invalid_input_encoding(self) -> None:
        self.script.write_text("process.stdin.pipe(process.stdout)")
        for backend in [None, self.extension]:
            with (
                self.subTest(native=backend is not None),
                patch.object(tex, "_rust_markdown_tex", backend),
                self.assertRaises(UnicodeEncodeError),
            ):
                tex.render_tex("\ud800")

    def test_missing_script_does_not_spawn(self) -> None:
        with patch.object(tex, "_rust_markdown_tex", self.extension):
            with (
                patch.object(self.extension, "run_katex_cli") as run,
                self.assertLogs(level="ERROR") as logs,
            ):
                self.assertIsNone(tex.render_tex("x"))
            run.assert_not_called()
        self.assertEqual(logs.output, ["ERROR:root:Cannot find KaTeX for latex rendering!"])

    def test_old_wheel_uses_python(self) -> None:
        self.script.write_text("process.stdout.write('fallback')")
        with patch.object(tex, "_rust_markdown_tex", SimpleNamespace()):
            self.assertEqual(tex.render_tex("x"), "fallback")

    def test_unavailable_process_isolation_uses_python(self) -> None:
        self.script.write_text("process.stdout.write('fallback')")
        with patch.object(tex, "_rust_markdown_tex", self.extension):
            with patch.object(
                self.extension, "run_katex_cli", side_effect=NotImplementedError
            ) as run:
                self.assertEqual(tex.render_tex("x"), "fallback")
            run.assert_called_once()

    def test_other_platforms_use_python(self) -> None:
        self.script.write_text("process.stdout.write('fallback')")
        with patch.object(tex, "_rust_markdown_tex", self.extension):
            with (
                patch.object(self.extension, "run_katex_cli") as run,
                patch.object(tex, "sys", SimpleNamespace(platform="darwin")),
            ):
                self.assertEqual(tex.render_tex("x"), "fallback")
            run.assert_not_called()

    def test_encoding_warnings_use_python(self) -> None:
        self.script.write_text("process.stdout.write('fallback')")
        with patch.object(tex, "_rust_markdown_tex", self.extension):
            with (
                patch.object(self.extension, "run_katex_cli") as run,
                patch.object(
                    tex,
                    "sys",
                    SimpleNamespace(
                        platform="linux", flags=SimpleNamespace(warn_default_encoding=1)
                    ),
                ),
            ):
                self.assertEqual(tex.render_tex("x"), "fallback")
            run.assert_not_called()

    def test_overridden_subprocess_uses_python(self) -> None:
        self.script.write_text("process.stdout.write('fallback')")
        with patch.object(tex, "_rust_markdown_tex", self.extension):
            with patch.object(self.extension, "run_katex_cli") as run:
                with patch.object(subprocess, "check_output", return_value=" mocked\n") as check:
                    self.assertEqual(tex.render_tex("x"), "mocked")
                    check.assert_called_once()
                with patch.object(subprocess, "Popen", wraps=subprocess.Popen) as popen:
                    self.assertEqual(tex.render_tex("x"), "fallback")
                    popen.assert_called_once()
            run.assert_not_called()

    @override_settings(KATEX_SERVER=True)
    def test_server_configuration_does_not_spawn(self) -> None:
        session = Mock()
        session.post.return_value = SimpleNamespace(status_code=200, content=b"<i>server</i>\n")
        with patch.object(tex, "_rust_markdown_tex", self.extension):
            with (
                patch.object(self.extension, "run_katex_cli") as run,
                patch.object(tex, "KatexSession", return_value=session),
            ):
                self.assertEqual(tex.render_tex("x"), "<i>server</i>")
            run.assert_not_called()

    def test_production_bundle_path(self) -> None:
        self.script.write_text("process.stdout.write('production')")
        with (
            self.settings(PRODUCTION=True),
            patch.object(tex, "static_path", return_value=str(self.script)) as static_path,
            patch.object(tex, "_rust_markdown_tex", self.extension),
        ):
            self.assertEqual(tex.render_tex("x"), "production")
        static_path.assert_called_once_with("webpack-bundles/katex-cli.js")

    def test_full_rendering_uses_native_cli_and_preserves_metadata(self) -> None:
        samples = [
            r"**formula** $$\frac{1}{2}$$ &amp; example.com",
            "```math\n\\int_a^b f(t)\\, dt = F(b) - F(a)\n```",
            r"$$\invalidcommand{<script>}$$ :smile: <time:2024-W01>",
        ]
        with self.settings(DEPLOY_ROOT=self.deploy_root):
            for content in samples:
                with self.subTest(content=content):
                    with patch.object(tex, "_rust_markdown_tex", None):
                        expected = markdown.do_convert(content, no_previews=True)
                    with patch.object(tex, "_rust_markdown_tex", self.extension):
                        with patch.object(
                            self.extension, "run_katex_cli", wraps=self.extension.run_katex_cli
                        ) as run:
                            actual = markdown.do_convert(content, no_previews=True)
                        self.assertGreater(run.call_count, 0)
                    self.assertEqual(actual, expected)
                    self.assertNotIn("<script>", actual.rendered_content)
