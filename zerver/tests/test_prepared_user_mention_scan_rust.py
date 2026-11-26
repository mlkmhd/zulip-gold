import re
from contextlib import nullcontext
from random import Random
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import mention


class PreparedUserMentionScanRustTest(SimpleTestCase):
    def test_exact_scan_preserves_set_iteration_and_wildcard_roles(self) -> None:
        backend = mention._rust_prepared_user_mention_scan
        witness = (
            patch.object(
                backend,
                "scan_prepared_markdown_user_mentions",
                wraps=backend.scan_prepared_markdown_user_mentions,
            )
            if backend is not None
            else nullcontext()
        )
        rng = Random(8013)
        names = ("Alice", "کاربر|123", "topic", "channel", "Everyone", "دو\nخط", "", "*")
        prefixes = ("", " ", "\u001c", "[", "x", "(", "/", "\t", "🙂")
        with patch.object(mention, "_rust_markdown_mentions", None), witness as native:
            for case in range(400):
                content = " ".join(
                    rng.choice(prefixes) + rng.choice(("@**", "@_**")) + rng.choice(names) + "**"
                    for _ in range(1 + case % 12)
                )
                with patch.object(mention, "_rust_prepared_user_mention_scan", None):
                    reference = mention.possible_mentions(content)
                actual = mention.possible_mentions(content)
                self.assertEqual(actual, reference)
                self.assertEqual(list(actual.mention_texts), list(reference.mention_texts))
            if native is not None:
                self.assertEqual(native.call_count, 400)

    def test_hooks_and_surrogates_keep_original_scanner(self) -> None:
        with patch.object(mention, "_rust_markdown_mentions", None):
            with patch.object(mention, "MENTIONS_RE", re.compile(r"\A\Z")):
                self.assertEqual(mention.possible_mentions("@**Alice**").mention_texts, set())
            self.assertEqual(
                mention.possible_mentions("@**الف\ud800**").mention_texts, {"الف\ud800"}
            )
