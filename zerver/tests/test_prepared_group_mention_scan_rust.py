import re
from contextlib import nullcontext
from random import Random
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import mention


class PreparedGroupMentionScanRustTest(SimpleTestCase):
    def test_exact_scan_keeps_order_and_silent_upgrade(self) -> None:
        backend = mention._rust_prepared_group_mention_scan
        witness = (
            patch.object(
                backend,
                "scan_prepared_markdown_group_mentions",
                wraps=backend.scan_prepared_markdown_group_mentions,
            )
            if backend is not None
            else nullcontext()
        )
        rng = Random(5291)
        names = ("Ops", "Build", "تست", "خط\nدوم", "Ops|31", "", "**")
        prefixes = ("", " ", "x", "[", "\u001e", "(", "/", "🧪")
        with patch.object(mention, "_rust_markdown_mentions", None), witness as native:
            self.assertEqual(
                list(
                    mention.possible_user_group_mentions("@_*Ops* @*Build* @*Ops* @_*Ops*").items()
                ),
                [("Ops", "non-silent"), ("Build", "non-silent")],
            )
            for case in range(360):
                content = " ".join(
                    rng.choice(prefixes) + rng.choice(("@*", "@_*")) + rng.choice(names) + "*"
                    for _ in range(1 + case % 12)
                )
                with patch.object(mention, "_rust_prepared_group_mention_scan", None):
                    reference = mention.possible_user_group_mentions(content)
                self.assertEqual(
                    list(mention.possible_user_group_mentions(content).items()),
                    list(reference.items()),
                )
            if native is not None:
                self.assertEqual(native.call_count, 361)

    def test_custom_scanner_and_surrogate_keep_python_behavior(self) -> None:
        with patch.object(mention, "_rust_markdown_mentions", None):
            with patch.object(mention, "USER_GROUP_MENTIONS_RE", re.compile(r"\A\Z")):
                self.assertEqual(mention.possible_user_group_mentions("@*Ops*"), {})
            self.assertEqual(
                mention.possible_user_group_mentions("@_*الف\ud800*"), {"الف\ud800": "silent"}
            )
