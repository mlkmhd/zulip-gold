from itertools import product
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import email_notifications as email


class EmailPlainSpoilerRustTest(SimpleTestCase):
    def test_fence_sequences_and_unicode_match_python(self) -> None:
        backend = email._rust_email_plain_spoilers
        if backend is None:
            self.skipTest("No prepared email spoiler ABI")
        lines = (
            "",
            "outside 🙂",
            "```spoiler header \ud800",
            "~~~ spoiler عنوان",
            "    ```spoiler indented",
            "INTERIOR-TEST-ONLY",
            "```python",
            "```",
            "~~~",
            "````",
        )
        for count in range(5):
            for snippet in product(lines, repeat=count):
                content = "\n".join(snippet)
                with patch.object(email, "_rust_email_plain_spoilers", None):
                    expected = email.fix_spoilers_in_text(content, "en")
                self.assertEqual(email.fix_spoilers_in_text(content, "en"), expected)

    def test_native_email_text_keeps_header_and_excludes_body(self) -> None:
        backend = email._rust_email_plain_spoilers
        if backend is None:
            self.skipTest("No prepared email spoiler ABI")
        body = "before\n```spoiler عنوان\nnot for email\n```\nafter\n"
        with patch.object(
            backend,
            "redact_prepared_email_plaintext_spoilers",
            wraps=backend.redact_prepared_email_plaintext_spoilers,
        ) as redact:
            result = email.fix_spoilers_in_text(body, "en")
        redact.assert_called_once()
        self.assertEqual(
            result,
            "before\n```spoiler عنوان\n(Open Zulip to see the spoiler content)\n```\nafter\n",
        )
        self.assertNotIn("not for email", result)
