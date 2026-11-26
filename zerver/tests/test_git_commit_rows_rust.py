from unittest import TestCase
from unittest.mock import Mock, patch

from zerver.lib.webhooks import git


class GitCommitRowsRustTest(TestCase):
    def test_respects_limit_unicode_and_lf_without_processing_discarded_rows(self) -> None:
        row = {"sha": "ΑΒΓΔΕΖΗΘΙΚΛΜ", "message": "line\r\nshould not appear"}
        data = [row] * git.COMMITS_LIMIT + [{"ignored": "no selected fields"}]
        rendered = git.get_commits_content(data, True)
        self.assertEqual(rendered.count("* line\r ([ΑΒΓΔΕΖΗΘΙΚΛ](None))\n"), git.COMMITS_LIMIT)
        self.assertTrue(rendered.endswith("[and 1 more commit(s)]"))
        self.assertNotIn("should not appear", rendered)
        with patch.object(git, "_rust_render_prepared_git_commit_rows", None):
            self.assertEqual(git.get_commits_content(data, True), rendered)

    def test_lookups_template_and_failure_order(self) -> None:
        calls: list[str] = []

        def short(sha: str) -> str:
            calls.append(f"sha:{sha}")
            return "short"

        def first(message: str) -> str:
            calls.append(f"msg:{message}")
            return "first"

        def format_row(**fields: object) -> str:
            calls.append("format")
            return " ok "

        template = Mock(format=Mock(side_effect=format_row))
        with (
            patch.object(git, "_commit_row_short_sha", short),
            patch.object(git, "_commit_row_first_lf_line", first),
            patch.object(git, "COMMIT_ROW_TEMPLATE", template),
            self.assertRaises(KeyError),
        ):
            git.get_commits_content([{"sha": "aaa", "message": "one"}, {"sha": "bbb"}])
        self.assertEqual(calls, ["sha:aaa", "msg:one", "format", "sha:bbb"])
        template.format.assert_called_once_with(
            commit_short_sha="short", commit_url=None, commit_msg="first"
        )

    def test_current_native_constructs_actual_rows(self) -> None:
        render = git._rust_render_prepared_git_commit_rows
        if render is None:
            self.skipTest("requires the current native commit row adapter")
        data = [{"sha": "abc", "message": "first", "url": "https://example.test/placeholder"}]
        with patch.object(git, "_rust_render_prepared_git_commit_rows", wraps=render) as native:
            self.assertEqual(
                git.get_commits_content(data), "* first ([abc](https://example.test/placeholder))"
            )
        native.assert_called_once_with(
            data,
            git.COMMITS_LIMIT,
            git.COMMIT_ROW_TEMPLATE,
            git._commit_row_short_sha,
            git._commit_row_first_lf_line,
        )
