from itertools import product
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import markdown
from zerver.lib.markdown.from_html import convert_html_to_markdown


class MarkdownAttachmentLinkRustTest(SimpleTestCase):
    def test_link_round_trip_uses_prepared_label_and_url(self) -> None:
        backend = markdown._attachment_link_backend
        if backend is None or not hasattr(backend, "format_prepared_markdown_attachment_link"):
            self.skipTest("No prepared Markdown attachment link ABI")
        for label, url in product(
            ["", "plain.txt", "[photo].png", "🌱[عکس]", "zero\x00character", chr(0xD800)],
            ["", "/user_uploads/1/a.jpg", "https://example.com/a?x=1", chr(0xDFFF)],
        ):
            with patch.object(markdown, "_attachment_link_backend", None):
                expected = markdown.get_markdown_link_for_url(label, url)
            self.assertEqual(markdown.get_markdown_link_for_url(label, url), expected)

        class FormattedURL(str):
            __slots__ = ()

            def __format__(self, spec: str) -> str:
                return super().__format__(spec).upper()

        self.assertEqual(
            markdown.get_markdown_link_for_url("[file]", FormattedURL("/test")),
            "[file](/TEST)",
        )
        self.assertIsNone(
            backend.format_prepared_markdown_attachment_link("file", FormattedURL("/test"))
        )

    def test_html_converter_keeps_its_normal_attachment_link(self) -> None:
        self.assertEqual(
            convert_html_to_markdown('<img src="https://example.com/img.png" alt="[عکس]">'),
            "[عکس](https://example.com/img.png)",
        )
