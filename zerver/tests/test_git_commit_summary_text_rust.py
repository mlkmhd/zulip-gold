import os
from importlib import import_module
from unittest import TestCase
from unittest.mock import patch

from zerver.lib.native_backend import supports_default_rust_backend
from zerver.lib.webhooks import git


class GitCommitSummaryTextRustTest(TestCase):
    def test_suffix_and_exact_legacy_trimming(self) -> None:
        try:
            finish = import_module("_zulip_backend").finish_git_commit_summary_text
        except (ImportError, AttributeError):
            self.skipTest("Rust Git commit summary formatter is not available")

        prefix = "syn"
        rows = f"{prefix}thetic"
        self.assertIs(finish(rows, None), rows)
        self.assertEqual(finish("متن😀\r\n\x1f\u3000", None), "متن😀")
        self.assertEqual(finish("  متن\u200b \n", None), "  متن\u200b")
        self.assertEqual(finish("rows \n", 0), "rows \n[and more commit(s)]")
        self.assertEqual(finish("rows\n", 1), "rows\n[and 1 more commit(s)]")
        self.assertEqual(finish("", 9), "[and 9 more commit(s)]")
        self.assertEqual(finish("", None), "")

        class Derived(str):
            __slots__ = ()

        for value in (b"rows", Derived("text"), None):
            self.assertIsNone(finish(value, None))
        for invalid in (True, -1, 2**90, "5", 5.0):
            self.assertIsNone(finish("rows", invalid))

    def test_all_surrogate_code_points_trim_and_append_like_python(self) -> None:
        try:
            module = import_module("_zulip_backend")
        except ImportError:
            self.skipTest("Wheel is absent")
        if getattr(module, "GIT_COMMIT_SUMMARY_API", None) != 1:
            self.skipTest("Wheel predates surrogate-safe commit summaries")
        finish = module.finish_git_commit_summary_text
        white = "".join(chr(value) for value in range(0x110000) if chr(value).isspace())
        for code in range(0xD800, 0xE000):
            row = f"آ{chr(code)}"
            self.assertIs(finish(row, None), row)
            source = row + white
            self.assertEqual(finish(source, None), source.rstrip())
            self.assertEqual(finish(source, 0), source + "[and more commit(s)]")
            self.assertEqual(finish(source, 2), source + "[and 2 more commit(s)]")

    def test_prepared_commit_rows_and_three_suffix_modes(self) -> None:
        row = {"sha": "abcdef0123456789", "message": "تغییر😀\r\nignored", "name": "Tester"}
        rendered = "* تغییر😀\r ([abcdef01234](None))\n"
        samples = [
            ([], False, ""),
            ([], True, "[and more commit(s)]"),
            ([row], False, rendered.rstrip()),
            ([row], True, rendered + "[and more commit(s)]"),
            ([row] * git.COMMITS_LIMIT, False, (rendered * git.COMMITS_LIMIT).rstrip()),
            (
                [row] * (git.COMMITS_LIMIT + 2),
                True,
                rendered * git.COMMITS_LIMIT + "[and 2 more commit(s)]",
            ),
        ]
        for rows, truncated, want in samples:
            with self.subTest(length=len(rows), truncated=truncated):
                self.assertEqual(git.get_commits_content(rows, truncated), want)

    def test_real_callsite_passes_only_rendered_text_and_original_decision(self) -> None:
        row = {"sha": "abcdefghijklmnop", "message": "summary\nignored", "name": "Tester"}
        known = "* summary ([abcdefghijk](None))\n"
        finish = git._rust_finish_git_commit_summary_text
        enabled = os.environ.get("ZULIP_RUST_GIT_COMMIT_SUMMARY_TEXT") == "1" or (
            supports_default_rust_backend()
            and getattr(git._rust_git_commit_summary, "GIT_COMMIT_SUMMARY_API", None) == 1
        )
        self.assertEqual(callable(finish), enabled)
        samples = [
            ([row], False, (known, None)),
            ([row], True, (known, 0)),
            ([row] * (git.COMMITS_LIMIT + 2), False, (known * git.COMMITS_LIMIT, 2)),
        ]
        for rows, truncated, expected_call in samples:
            with self.subTest(mode=expected_call[1]):
                if finish is None:
                    self.assertTrue(
                        git.get_commits_content(rows, truncated).startswith(known.rstrip())
                    )
                else:
                    with patch.object(
                        git, "_rust_finish_git_commit_summary_text", wraps=finish
                    ) as spy:
                        self.assertEqual(
                            git.get_commits_content(rows, truncated), finish(*expected_call)
                        )
                    spy.assert_called_once_with(*expected_call)

    def test_decline_malformed_template_override_and_legacy_error(self) -> None:
        row = {"sha": "abcdefghijklmnop", "message": "summary\ud800\nignored", "name": "Tester"}
        want = "* summary\ud800 ([abcdefghijk](None))\n[and more commit(s)]"
        self.assertEqual(git.get_commits_content([row], True), want)
        for fallback in (None, object(), lambda *args: None, lambda *args: ("bad",)):
            with patch.object(git, "_rust_finish_git_commit_summary_text", fallback):
                self.assertEqual(git.get_commits_content([row], True), want)
        with (
            patch.object(
                git, "COMMITS_MORE_THAN_LIMIT_TEMPLATE", "  (remains {commits_number}) \n"
            ),
            patch.object(git, "_rust_finish_git_commit_summary_text") as spy,
        ):
            self.assertEqual(
                git.get_commits_content([row], True),
                "* summary\ud800 ([abcdefghijk](None))\n (remains )",
            )
            spy.assert_not_called()
        with self.assertRaisesRegex(KeyError, "sha"):
            git.get_commits_content([{"message": "not selected"}])

    def test_pure_push_display_with_related_git_flags(self) -> None:
        rows = [
            {"sha": "a123456789bc", "message": "headline\nbody", "name": "Alpha"},
            {"sha": "b123456789bc", "message": "word", "name": "Beta"},
        ]
        result = git.get_push_commits_event_message("Tester", None, "synthetic", rows, True)
        self.assertEqual(
            result,
            "Tester pushed 2 commits to branch synthetic. Commits by Alpha (1) and Beta (1).\n\n"
            "* headline ([a123456789b](None))\n* word ([b123456789b](None))\n[and more commit(s)]",
        )
