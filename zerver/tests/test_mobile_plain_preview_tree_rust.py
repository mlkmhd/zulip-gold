from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import native_push_text
from zerver.lib.push_notifications import get_mobile_push_content


class MobilePlainPreviewTreeRustTest(SimpleTestCase):
    def test_native_preview_preserves_text_and_tails_in_document_order(self) -> None:
        backend = native_push_text._plain_preview_tree_backend
        if backend is None or not hasattr(backend, "render_plain_mobile_preview_tree"):
            self.skipTest("No mobile plaintext preview tree ABI")
        for html in (
            "simple text",
            "<p>Hello <strong>world</strong> <a href='https://example.test/'>link</a>.</p>",
            "<p>🌿<span>فارسی <em>and nested</em> tail</span> final</p><p>second</p>",
            "<ul><li>one</li><li>two <code>with inset</code> tail</li></ul>",
        ):
            with patch.object(native_push_text, "_plain_preview_tree_backend", None):
                expected = get_mobile_push_content(html)
            with patch.object(
                backend,
                "render_plain_mobile_preview_tree",
                wraps=backend.render_plain_mobile_preview_tree,
            ) as witness:
                actual = get_mobile_push_content(html)
            witness.assert_called_once()
            self.assertEqual(actual, expected)
        self.assertIsNone(
            backend.render_plain_mobile_preview_tree([(0, "root", ""), (2, "bad", "")])
        )

    def test_special_subtree_never_crosses_the_simple_native_boundary(self) -> None:
        backend = native_push_text._plain_preview_tree_backend
        if backend is None or not hasattr(backend, "render_plain_mobile_preview_tree"):
            self.skipTest("No mobile plaintext preview tree ABI")
        for html in (
            '<p>Hello <span class="emoji emoji-1f642"></span></p>',
            (
                '<div class="spoiler-block"><div class="spoiler-header">Title</div>'
                '<div class="spoiler-content">synthetic hidden text</div></div>'
            ),
            "<blockquote>quote</blockquote>",
            "<ol><li>one</li></ol>",
            "<p><img alt='image fallback'></p>",
        ):
            with patch.object(native_push_text, "_plain_preview_tree_backend", None):
                expected = get_mobile_push_content(html)
            with patch.object(
                backend, "render_plain_mobile_preview_tree", side_effect=AssertionError
            ) as witness:
                self.assertEqual(get_mobile_push_content(html), expected)
            witness.assert_not_called()
