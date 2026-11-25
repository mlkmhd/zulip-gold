from importlib import import_module
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from zerver.lib.webhooks import git


class CommitDisplayRustTest(TestCase):
    def test_scalar_projection_and_ranking(self) -> None:
        try:
            module = import_module("_zulip_backend")
            project = module.project_commit_display_row
            rank = module.rank_commit_display_names
        except (ImportError, AttributeError):
            self.skipTest("Rust commit display helpers are not available")

        short = "tag:synthetic".removeprefix("tag:")
        first_line = "tag:A synthetic first_line".removeprefix("tag:")
        row = project(short, first_line)
        self.assertIs(row[0], short)
        self.assertIs(row[1], first_line)
        self.assertEqual(project("😀" * 12, "first_line\r\nsecond"), ("😀" * 11, "first_line\r"))
        self.assertEqual(project("", "\u2028first\nnext"), ("", "\u2028first"))
        self.assertEqual(project("", "\n"), ("", ""))

        first = "one:synthetic A".removeprefix("one:")
        second = "two:synthetic A".removeprefix("two:")
        names = ["others", second, "😀", "é", "a", first, "others", second]
        result = rank(names, 2)
        self.assertEqual(result, [(first, 3), ("others", 2), ("others", 3)])
        self.assertIs(result[0][0], second)
        self.assertEqual(rank(["😀", "é", "a"], 3), [("a", 1), ("é", 1), ("😀", 1)])
        self.assertEqual(rank(names, 0), [("others", 8)])
        self.assertEqual(rank([], 0), [])

        class Derived(str):
            __slots__ = ()

        self.assertIsNone(project(Derived("x"), "first_line"))
        self.assertIsNone(rank([Derived("x")], 2))
        self.assertIsNone(rank(["a"], -1))
        self.assertIsNone(rank(["a"], True))

        if getattr(module, "COMMIT_DISPLAY_API", None) == 1:
            surrogate_points = list(map(chr, range(0xD800, 0xE000)))
            special = "".join(surrogate_points)
            subject = special + "\r"
            self.assertEqual(project(special, subject + "\nignored"), (special[:11], subject))
            names = ["\ud7ff", "\ue000", "🙂", "a", *surrogate_points]
            self.assertEqual(rank(names, len(names)), [(name, 1) for name in sorted(names)])

    def test_existing_display_row_keeps_field_order_and_template(self) -> None:
        original = git._rust_project_commit_display_row
        spy = Mock(wraps=original) if original is not None else None
        commit = MagicMock()
        sha = "🙂" * 12
        message = "\u2028first_line\r\nsecond"
        url = "synthetic-reference"
        commit.__getitem__.side_effect = {"sha": sha, "message": message}.__getitem__
        commit.get.return_value = url
        template = Mock()
        template.format.return_value = "prepared row\n"
        with (
            patch.object(git, "COMMIT_ROW_TEMPLATE", template),
            patch.object(git, "_rust_project_commit_display_row", spy),
        ):
            self.assertEqual(git.get_commits_content([commit]), "prepared row")
        self.assertEqual(
            [(entry[0], entry.args, entry.kwargs) for entry in commit.mock_calls],
            [
                ("__getitem__", ("sha",), {}),
                ("get", ("url",), {}),
                ("__getitem__", ("message",), {}),
            ],
        )
        template.format.assert_called_once_with(
            commit_short_sha="🙂" * 11, commit_url=url, commit_msg="\u2028first_line\r"
        )
        if spy is not None:
            self.assertEqual([item.args for item in spy.call_args_list], [(sha, ""), ("", message)])

    def test_ranking_uses_already_extracted_names_and_keeps_first_identity(self) -> None:
        original = git._rust_rank_commit_display_names
        spy = Mock(wraps=original) if original is not None else None
        first = "a:synthetic name".removeprefix("a:")
        repeated = "b:synthetic name".removeprefix("b:")
        literal = "fixture:others".removeprefix("fixture:")
        names = [first, "é", literal, "😀", "a", repeated, first, literal]
        rows = [{"name": name} for name in names]
        with patch.object(git, "_rust_rank_commit_display_names", spy):
            ranked = git.get_all_committers(rows)
        self.assertEqual(ranked, [(first, 3), ("others", 2), ("a", 1), ("others", 2)])
        self.assertIs(ranked[0][0], first)
        self.assertIs(ranked[1][0], literal)
        self.assertIsNot(ranked[3][0], literal)
        if spy is not None:
            spy.assert_called_once_with(names, git.PUSH_COMMITTERS_LIMIT_INFO)

    def test_decline_and_existing_sha_override_keep_python_fallback(self) -> None:
        with (
            patch.object(git, "get_short_sha", return_value="original override") as short,
            patch.object(git, "_rust_project_commit_display_row", return_value=None) as project,
            patch.object(git, "_rust_rank_commit_display_names", return_value=None) as rank,
        ):
            self.assertEqual(git._commit_row_short_sha("sha"), "original override")
            project.assert_not_called()
            self.assertEqual(git._commit_row_first_lf_line("\ud800\r\nnext"), "\ud800\r")
            self.assertEqual(
                git.get_all_committers([{"name": "é"}, {"name": "a"}]), [("a", 1), ("é", 1)]
            )
            with self.assertRaises(KeyError) as missing:
                git.get_all_committers([{}])
            self.assertEqual(missing.exception.args, ("name",))
        short.assert_called_once_with("sha")
        project.assert_called_once_with("", "\ud800\r\nnext")
        rank.assert_called_once_with(["é", "a"], git.PUSH_COMMITTERS_LIMIT_INFO)
