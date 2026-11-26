from types import ModuleType
from unittest.mock import patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import url_encoding
from zerver.lib.url_encoding import encode_user_full_name_and_id


class ASCIIUserSlugRustTest(SimpleTestCase):
    def test_every_ascii_character_at_edges_and_in_separator_runs(self) -> None:
        cases = [
            value
            for n in range(128)
            for c in (chr(n),)
            for value in (f"A{c}A", f"{c}A{c}", f"A{c}{c}B")
        ]
        for value in cases:
            with patch.object(url_encoding, "_rust_ascii_user_narrow_slug", ModuleType("old")):
                expected = encode_user_full_name_and_id(value, -12, True)
            self.assertEqual(encode_user_full_name_and_id(value, -12, True), expected)

    def test_unicode_wide_id_and_dynamic_strip_keep_python_contract(self) -> None:
        class CustomName(str):
            __slots__ = ()

            @override
            def strip(self, chars: str | None = None, /) -> str:
                return "Custom / Value"

        for name, pk in (
            ("آرش　سلام", 12),
            ("A \u200d 😀", 12),
            ("A  B", 2**92),
            (CustomName("unchanged"), 12),
        ):
            with patch.object(url_encoding, "_rust_ascii_user_narrow_slug", None):
                old = encode_user_full_name_and_id(name, pk)
            self.assertEqual(encode_user_full_name_and_id(name, pk), old)

    def test_current_native_only_handles_supported_cases(self) -> None:
        backend = url_encoding._rust_ascii_user_narrow_slug
        if backend is None:
            self.skipTest("requires current ASCII user-link formatter")
        with patch.object(
            backend, "encode_narrow_user_slug_ascii", wraps=backend.encode_narrow_user_slug_ascii
        ) as encode:
            self.assertEqual(
                encode_user_full_name_and_id(" User  Name ", 12, True), "dm/12-User-Name"
            )
        encode.assert_called_once_with(" User  Name ", 12, True)
        self.assertIsNone(backend.encode_narrow_user_slug_ascii("آرش", 12, True))
        self.assertIsNone(backend.encode_narrow_user_slug_ascii("User", 2**100, True))
