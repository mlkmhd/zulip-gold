from itertools import product
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import native_push_text
from zerver.lib.push_notifications import get_mobile_push_content


class MobileQuoteCollapseRustTest(SimpleTestCase):
    def test_native_matches_every_small_prepared_quote_mask(self) -> None:
        backend = native_push_text._quote_collapse_backend
        if backend is None or not hasattr(backend, "plan_mobile_quote_collapse"):
            self.skipTest("No mobile quote collapse ABI")
        plan = backend.plan_mobile_quote_collapse
        for length in range(9):
            for values in product((False, True), repeat=length):
                expected = []
                if any(values) and not all(values):
                    expected = [
                        (index > 0 and values[index - 1]) if value else None
                        for index, value in enumerate(values)
                    ]
                self.assertEqual(plan(list(values)), expected)
        self.assertIsNone(plan((True, False)))
        self.assertIsNone(plan([1, False]))

    def test_preview_groups_are_unchanged_and_only_flags_reach_rust(self) -> None:
        backend = native_push_text._quote_collapse_backend
        if backend is None or not hasattr(backend, "plan_mobile_quote_collapse"):
            self.skipTest("No mobile quote collapse ABI")
        for blocks in product((False, True), repeat=4):
            preview = "".join(
                f"<blockquote>quote-{index}</blockquote>" if block else f"<p>reply-{index}</p>"
                for index, block in enumerate(blocks)
            )
            with patch.object(native_push_text, "_quote_collapse_backend", None):
                expected = get_mobile_push_content(preview)
            with patch.object(
                backend, "plan_mobile_quote_collapse", wraps=backend.plan_mobile_quote_collapse
            ) as witness:
                actual = get_mobile_push_content(preview)
            witness.assert_called_once_with(list(blocks))
            self.assertEqual(actual, expected)
