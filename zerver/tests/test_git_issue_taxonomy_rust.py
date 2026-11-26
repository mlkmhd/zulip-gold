from unittest import TestCase
from unittest.mock import patch

from zerver.lib.webhooks import git


class GitIssueTaxonomyRustTest(TestCase):
    def test_label_format_and_empty_title(self) -> None:
        fn = git.get_issue_labeled_or_unlabeled_event_message
        self.assertEqual(
            fn("Demo", "added", "issue-url", 3, "سبز", "person"),
            "[Demo](person) added the سبز label to [Issue #3](issue-url).",
        )
        self.assertEqual(
            fn("Demo", "removed", "issue-url", 3, "سبز", "person", ""),
            "[Demo](person) removed the سبز label from [Issue #3 ](issue-url).",
        )

    def test_milestone_uses_the_current_template_and_preserves_objects(self) -> None:
        fn = git.get_issue_milestoned_or_demilestoned_event_message
        self.assertEqual(
            fn("Demo", "added", "issue", 0, "میلستون", "link", "person"),
            "[Demo](person) added milestone [میلستون](link) to [issue #0](issue).",
        )
        with patch.object(
            git,
            "ISSUE_MILESTONED_OR_DEMILESTONED_MESSAGE_TEMPLATE_WITH_TITLE",
            "{preposition}:{milestone_name}:{id}:{title}",
        ):
            self.assertEqual(
                fn("Demo", "removed", "issue", 2**120, "میلستون", "link", "person", ""),
                f"from:میلستون:{2**120}:",
            )

    def test_both_actual_call_sites_use_current_native(self) -> None:
        render = git._rust_render_prepared_issue_taxonomy
        if render is None:
            self.skipTest("requires the current native issue taxonomy renderer")
        with patch.object(git, "_rust_render_prepared_issue_taxonomy", wraps=render) as native:
            git.get_issue_labeled_or_unlabeled_event_message("U", "added", "issue", 1, "L", "u")
            git.get_issue_milestoned_or_demilestoned_event_message(
                "U", "removed", "issue", 1, "M", "m", "u"
            )
        self.assertEqual([record.args[1] for record in native.call_args_list], [False, True])
