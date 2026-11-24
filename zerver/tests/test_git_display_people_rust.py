import os
from importlib import import_module
from unittest import TestCase
from unittest.mock import Mock, patch

from typing_extensions import override

from zerver.lib.webhooks import git


class GitDisplayPeopleRustTest(TestCase):
    def test_only_literal_display_text(self) -> None:
        try:
            module = import_module("_zulip_backend")
            joined = module.join_git_display_assignees
            committers = module.format_git_display_committers
        except (ImportError, AttributeError):
            self.skipTest("Rust Git display people formatters are not available")

        first = "tag:synthetic".removeprefix("tag:")
        self.assertIs(joined([first]), first)
        self.assertEqual(joined(["", "فارسی", "😀"]), ", فارسی and 😀")
        self.assertEqual(joined(["one", "one"]), "one and one")
        self.assertEqual(committers([(first, 3)]), "Commits by synthetic (3)")
        self.assertEqual(
            committers([(first, 3), ("فارسی", 0), ("", -1), ("😀", 2)]),
            "Commits by synthetic (3), فارسی (0),  (-1) and 😀 (2)",
        )

        class Derived(str):
            __slots__ = ()

        invalid_names: tuple[object, ...] = (
            [],
            ["plain", Derived("custom")],
            ["\ud800"],
            ("a", "b"),
        )
        for invalid_name in invalid_names:
            self.assertIsNone(joined(invalid_name))
        invalid_counts: tuple[object, ...] = (
            [],
            [("one",)],
            [["one", 1]],
            [("one", True)],
            [("one", 2**90)],
            [("\ud800", 1)],
            [(Derived("test"), 5)],
        )
        for invalid_count in invalid_counts:
            self.assertIsNone(committers(invalid_count))

    def test_assignee_extraction_and_singleton_format_dispatch_remain_python(self) -> None:
        native = git._rust_join_git_display_assignees
        if os.environ.get("ZULIP_RUST_GIT_DISPLAY_PEOPLE") != "1":
            self.assertIsNone(native)
        spy = Mock(wraps=native) if native is not None else None

        class Styled(str):
            __slots__ = ()

            @override
            def __format__(self, format_spec: str) -> str:
                return "as supplied"

        with patch.object(git, "_rust_join_git_display_assignees", spy):
            self.assertEqual(
                git.get_assignee_string([{"username": "آ"}, {"username": ""}, {"username": "😀"}]),
                "آ,  and 😀",
            )
            self.assertEqual(git.get_assignee_string([{"username": Styled("raw")}]), "as supplied")
            with self.assertRaises(IndexError):
                git.get_assignee_string([])
            with self.assertRaises(KeyError) as missing:
                git.get_assignee_string([{"username": "present"}, {}])
            self.assertEqual(missing.exception.args, ("username",))
        if spy is not None:
            spy.assert_called_once_with(["آ", "", "😀"])

    def test_ranked_committer_details_only_in_the_original_display_branch(self) -> None:
        native = git._rust_format_git_display_committers
        if os.environ.get("ZULIP_RUST_GIT_DISPLAY_PEOPLE") != "1":
            self.assertIsNone(native)
        spy = Mock(wraps=native) if native is not None else None
        rows = [{"name": "synthetic"}]
        picked = [("late", 2), ("early", 7), ("others", 1)]
        with (
            patch.object(git, "get_all_committers", return_value=picked) as rank,
            patch.object(git, "get_commits_content", return_value="prepared rows") as content,
            patch.object(git, "_rust_format_git_display_committers", spy),
        ):
            actual = git.get_push_commits_event_message("fixture", None, "trunk", rows)
            self.assertEqual(
                actual,
                "fixture pushed 1 commit to branch trunk. "
                "Commits by late (2), early (7) and others (1).\n\nprepared rows",
            )
            rank.assert_called_once_with(rows)
            content.assert_called_once_with(rows, False)
            rank.return_value = [("fixture", 1)]
            self.assertEqual(
                git.get_push_commits_event_message("fixture", None, "trunk", rows),
                "fixture pushed 1 commit to branch trunk.\n\nprepared rows",
            )
        if spy is not None:
            spy.assert_called_once_with(picked)

    def test_decline_and_overridden_prefix_keep_the_legacy_format(self) -> None:
        rows = [{"name": "synthetic"}]
        picked = [("آ", 2), ("second", 1)]
        prefix = Mock()
        prefix.format.return_value = "original custom prefix"
        with (
            patch.object(git, "get_all_committers", return_value=picked),
            patch.object(git, "get_commits_content", return_value="rows"),
            patch.object(git, "_rust_format_git_display_committers", return_value=None) as native,
        ):
            self.assertIn(
                "Commits by آ (2) and second (1).",
                git.get_push_commits_event_message("fixture", None, "trunk", rows),
            )
            native.assert_called_once_with(picked)
            native.reset_mock()
            with patch.object(git, "PUSH_COMMITS_MESSAGE_EXTENSION", prefix):
                self.assertIn(
                    "original custom prefix.",
                    git.get_push_commits_event_message("fixture", None, "trunk", rows),
                )
            native.assert_not_called()
        prefix.format.assert_called_once_with("آ (2) and second (1)")
