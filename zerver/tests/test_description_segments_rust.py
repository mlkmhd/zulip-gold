from collections import UserDict
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import html_to_text


class DescriptionSegmentsRustTest(SimpleTestCase):
    def test_real_description_output_and_unread_content_bound(self) -> None:
        backend = html_to_text._description_segments_backend
        if backend is None or not hasattr(backend, "collect_prepared_description_segments"):
            self.skipTest("No prepared description segments ABI")
        html = (
            '<div class="admonition"><p>hidden warning</p></div>'
            '<div class="tabbed-section"><p>hidden navigation</p></div>'
            "<p>one &amp; two</p><li>فارسی</li><p>🌿</p>"
        )
        tags = {"p": " | ", "li": " * "}
        with patch.object(html_to_text, "_description_segments_backend", None):
            expected = html_to_text.html_to_text(html, tags)
        with patch.object(
            backend,
            "collect_prepared_description_segments",
            wraps=backend.collect_prepared_description_segments,
        ) as witness:
            self.assertEqual(html_to_text.html_to_text(html, tags), expected)
        witness.assert_called_once()
        self.assertNotIn("hidden", expected)
        self.assertEqual(html_to_text.html_to_text(html, UserDict(tags)), expected)

        def unbounded() -> object:
            yield "p", "😀" * 500
            yield "p", "one more"
            raise AssertionError("read text after description cutoff")

        collect = backend.collect_prepared_description_segments
        self.assertEqual(collect(unbounded(), tags), "😀" * 500 + " | one more")
        self.assertEqual(
            collect(iter([("p", chr(0xD800)), ("li", "x")]), tags), chr(0xD800) + " * x"
        )
