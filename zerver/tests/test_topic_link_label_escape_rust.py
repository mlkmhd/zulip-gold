from itertools import product
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import topic_link_util


class TopicLinkLabelEscapeRustTest(SimpleTestCase):
    def test_native_escape_matches_unicode_and_nonoverlapping_dollar_pairs(self) -> None:
        backend = topic_link_util._caption_escape_backend
        if backend is None or not hasattr(backend, "escape_exact_topic_link_label"):
            self.skipTest("No topic link label escape ABI")
        samples = ("", chr(96), ">", "*", "&", "$", "[", "]", "🌱", "فارسی", chr(0xD800))
        for parts in product(samples, repeat=3):
            text = "".join(parts)
            with patch.object(topic_link_util, "_caption_escape_backend", None):
                expected = topic_link_util.escape_invalid_stream_topic_characters(text)
            self.assertEqual(topic_link_util.escape_invalid_stream_topic_characters(text), expected)

        plain = "با رشتهٔ یونیکد"
        with patch.object(
            backend, "escape_exact_topic_link_label", wraps=backend.escape_exact_topic_link_label
        ) as witness:
            self.assertIs(topic_link_util.escape_invalid_stream_topic_characters(plain), plain)
        witness.assert_called_once_with(plain)

        with patch.dict(topic_link_util.escape_mapping, {"&": "(custom)"}):
            self.assertEqual(
                topic_link_util.escape_invalid_stream_topic_characters("a&b"), "a(custom)b"
            )
