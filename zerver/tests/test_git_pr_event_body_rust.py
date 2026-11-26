from unittest import TestCase
from unittest.mock import Mock, patch

from zerver.lib.webhooks import git


class GitPrEventBodyRustTest(TestCase):
    def test_branch_priority_and_optional_quote(self) -> None:
        with patch.object(git, "get_unused_fence", return_value="FENCE") as marker:
            text = git.get_pull_request_event_message(
                user_name="Demo",
                action="approval",
                url="https://example.test/pr",
                number=12,
                title="Done!",
                target_branch="target",
                base_branch="base",
                reviewer="Reviewer",
                message="Synthetic note",
                emoji="✨",
            )
        self.assertEqual(
            text,
            "✨ Demo added their approval for [PR #12 Done!](https://example.test/pr)"
            " from \u0060target\u0060 to \u0060base\u0060 (assigned reviewers: Reviewer):\n\n"
            "FENCE quote\nSynthetic note\nFENCE",
        )
        marker.assert_called_once_with("Synthetic note")

    def test_empty_title_keeps_original_error_and_side_effect_checks(self) -> None:
        with self.assertRaisesRegex(IndexError, "^string index out of range$"):
            git.get_pull_request_event_message(user_name="Demo", action="opened", url="", title="")
        assignees = [{"username": "Demo"}]

        def pick(_data: object) -> str:
            assignees.clear()
            return "should not appear"

        with patch.object(git, "get_assignee_string", side_effect=pick) as choose:
            text = git.get_pull_request_event_message(
                user_name="Demo", action="opened", url="", title="Done!", assignees=assignees
            )
        self.assertEqual(text, "Demo opened [PR Done!]()")
        choose.assert_called_once()

    def test_supported_inputs_dispatch_but_str_subclass_declines(self) -> None:
        native = git._rust_build_prepared_pr_event_body
        if native is None:
            self.skipTest("requires the current native pull-request body builder")
        baseline = git.get_pull_request_event_message(user_name="Demo", action="closed", url="")
        with patch.object(git, "_rust_build_prepared_pr_event_body", wraps=native) as render:
            self.assertEqual(
                git.get_pull_request_event_message(user_name="Demo", action="closed", url=""),
                baseline,
            )
        render.assert_called_once()
        reject = Mock(side_effect=AssertionError("str subclass belongs in the compatibility path"))

        class Title(str):
            __slots__ = ()

        with patch.object(git, "_rust_build_prepared_pr_event_body", reject):
            self.assertEqual(
                git.get_pull_request_event_message(
                    user_name="Demo", action="closed", url="", title=Title("X.")
                ),
                "Demo closed [PR X.]()",
            )
        reject.assert_not_called()
