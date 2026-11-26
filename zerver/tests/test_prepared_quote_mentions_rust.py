import re
from contextlib import nullcontext
from random import Random
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import markdown, mention


class PreparedQuoteMentionsRustTest(SimpleTestCase):
    def test_two_scans_preserve_exact_quoted_plain_text(self) -> None:
        backend = markdown._rust_prepared_quote_mentions
        witness = (
            patch.object(
                backend,
                "silence_prepared_markdown_quote_mentions",
                wraps=backend.silence_prepared_markdown_quote_mentions,
            )
            if backend is not None
            else nullcontext()
        )
        sample = (
            "نقل‌قول",
            "@**Alice**",
            "@_*Ops*",
            "@*Build*",
            "@**topic**",
            "@_**Name|21**",
            "x@**not**",
            "@**without",
            "@****",
            "\u001c@**دو\nخط**",
            "@*👋*",
        )
        rng = Random(1801)
        with patch.object(markdown, "_rust_markdown_quote_mentions", None), witness as native:
            for case in range(225):
                line = " ".join(rng.choice(sample) for _ in range(case % 11 + 1))
                with patch.object(markdown, "_rust_prepared_quote_mentions", None):
                    expected = markdown._silence_blockquote_mentions(line, standard=True)
                self.assertEqual(
                    markdown._silence_blockquote_mentions(line, standard=True), expected
                )
            unchanged = "نقل‌قول بدون اشاره"
            self.assertIs(
                markdown._silence_blockquote_mentions(unchanged, standard=True), unchanged
            )
            if native is not None:
                self.assertEqual(native.call_count, 226)

    def test_odd_text_and_custom_regex_use_original_callbacks(self) -> None:
        with patch.object(markdown, "_rust_markdown_quote_mentions", None):
            with patch.object(mention, "MENTIONS_RE", re.compile(r"\A\Z")):
                self.assertEqual(
                    markdown._silence_blockquote_mentions("@**Alice**", standard=True), "@**Alice**"
                )
            self.assertEqual(
                markdown._silence_blockquote_mentions("@**الف\ud800** @*Ops*", standard=True),
                "@_**الف\ud800** @_*Ops*",
            )
