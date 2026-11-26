from unittest import TestCase
from unittest.mock import Mock, patch

from zerver.lib.webhooks import git


class GitCommitCommentRustTest(TestCase):
    def test_empty_message_and_nested_code_block_are_unchanged(self) -> None:
        url = "https://example.test/c/sample"
        sha = "🌱" * 11 + "extra"
        base = f"User commented on [{'🌱' * 11}]({url})"
        self.assertEqual(
            git.get_commits_comment_action_message("User", "commented", url, sha), base + "."
        )
        message = "sample\n   " + chr(96) * 4 + " python\nending"
        fence = chr(96) * 5
        self.assertEqual(
            git.get_commits_comment_action_message("User", "commented", url, sha, message),
            f"{base}:\n{fence} quote\n{message}\n{fence}",
        )

    def test_uses_same_templates_and_preserves_truthiness_read_order(self) -> None:
        calls: list[str] = []

        class Alternating(str):
            __slots__ = ()

            def __bool__(self) -> bool:
                calls.append("truth")
                return len(calls) == 1

        choose_fence = Mock(side_effect=AssertionError("second truthiness check should be false"))
        with (
            patch.object(git, "COMMITS_COMMENT_MESSAGE_TEMPLATE", "{user_name}/{sha}"),
            patch.object(git, "get_short_sha", return_value="abc") as short,
            patch.object(git, "get_unused_fence", choose_fence),
        ):
            message = git.get_commits_comment_action_message(
                "User", "added", "url", "saved", Alternating("note")
            )
        self.assertEqual(calls, ["truth", "truth"])
        self.assertEqual(message, "User/abc:")
        short.assert_called_once_with("saved")
        choose_fence.assert_not_called()

    def test_actual_native_path_passes_existing_fence_callback(self) -> None:
        fn = git._rust_build_prepared_git_comment_message
        if fn is None:
            self.skipTest("requires the current native Git comment builder")
        with (
            patch.object(git, "_rust_build_prepared_git_comment_message", wraps=fn) as native,
            patch.object(git, "get_unused_fence", return_value="FENCE") as choose,
        ):
            message = git.get_commits_comment_action_message("User", "added", "url", "sha", "note")
        self.assertEqual(message, "User added on [sha](url):\nFENCE quote\nnote\nFENCE")
        native.assert_called_once()
        choose.assert_called_once_with("note")
