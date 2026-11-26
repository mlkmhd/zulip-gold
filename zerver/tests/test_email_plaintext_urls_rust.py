from itertools import product
from random import Random
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import email_notifications


class EmailPlaintextUrlsRustTest(SimpleTestCase):
    def test_native_matches_existing_url_presentation_rules(self) -> None:
        backend = email_notifications._plaintext_url_backend
        if backend is None or not hasattr(backend, "format_prepared_email_plaintext_urls"):
            self.skipTest("No prepared email plaintext URLs ABI")
        rng = Random(9047)
        parts = ("[img]", "(", ")", "x", "", "/user_uploads/file", " ", chr(28), "\n", "[", "]")
        cases = [
            "",
            "See [img](/user_uploads/f.txt)",
            "[]() [image](https://example.test/p) /user_uploads/",
            "[img](https://example.test/1)[img](/user_uploads/one)",
            "فارسی [عکس](/user_uploads/a)",
            chr(0xD800),
        ] + ["".join(rng.choices(parts, k=12)) for _ in range(700)]
        for content, base_url in product(
            cases, ("https://zulip.example.test", "https://example.test/$")
        ):
            with patch.object(email_notifications, "_plaintext_url_backend", None):
                expected = email_notifications._format_plaintext_message_urls(content, base_url)
            self.assertEqual(
                email_notifications._format_plaintext_message_urls(content, base_url), expected
            )

        reference = backend.format_prepared_email_plaintext_urls
        with patch.object(
            backend, "format_prepared_email_plaintext_urls", wraps=reference
        ) as native:
            self.assertEqual(
                email_notifications._format_plaintext_message_urls(
                    "See [img](/user_uploads/f.txt)", "https://zulip.example.test"
                ),
                "See https://zulip.example.test/user_uploads/f.txt",
            )
        native.assert_called_once()
        self.assertIsNone(reference("فارسی", "https://zulip.example.test"))
