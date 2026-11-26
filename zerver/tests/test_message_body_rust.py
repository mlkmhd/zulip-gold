from contextlib import nullcontext
from unittest.mock import patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import message
from zerver.lib.exceptions import JsonableError


class ExactMessageBodyRustTest(SimpleTestCase):
    def test_exact_message_body_matches_text_and_error_precedence(self) -> None:
        backend = message._rust_exact_message_body
        witness = (
            patch.object(
                backend, "normalize_exact_message_body", wraps=backend.normalize_exact_message_body
            )
            if backend is not None
            else nullcontext()
        )

        def outcome(value: str) -> tuple[str, str]:
            try:
                return "body", message.normalize_body(value)
            except JsonableError as error:
                return "error", str(error)

        bodies = [
            "",
            "\n \u001f\u3000",
            "\n\n leading\n\r\t",
            "\r\n preserved\u00a0",
            "آ😀\ud800\u3000",
            "🙂" * 40 + "\udfff",
            "a" * 65 + "\0",
            "\0\u00a0",
        ]
        limits = (-(2**63), -1, 0, 19, 20, 21, 22, 40, 2**63 - 1)
        with patch.object(message, "_rust_message_body", None), witness as native:
            for limit in limits:
                with self.settings(MAX_MESSAGE_LENGTH=limit):
                    for value in bodies:
                        with self.subTest(limit=limit, body=repr(value)):
                            with patch.object(message, "_rust_exact_message_body", None):
                                expected = outcome(value)
                            self.assertEqual(outcome(value), expected)
            unchanged = "سلام😀\ud800"
            with self.settings(MAX_MESSAGE_LENGTH=100):
                self.assertIs(message.normalize_body(unchanged), unchanged)
            if native is not None:
                self.assertEqual(native.call_count, len(limits) * len(bodies) + 1)

    def test_subtype_and_custom_truncator_remain_in_python(self) -> None:
        class CustomBody(str):
            __slots__ = ()

            @override
            def rstrip(self, chars: str | None = None) -> str:
                return "custom"

        self.assertEqual(message.normalize_body(CustomBody("original")), "custom")
        with patch.object(message, "truncate_content", return_value="changed") as truncate:
            self.assertEqual(message.normalize_body("\nnormal\u3000"), "changed")
            truncate.assert_called_once()
            self.assertEqual(truncate.call_args.args[0], "normal")
