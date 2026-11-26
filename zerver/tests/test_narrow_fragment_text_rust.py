from types import ModuleType
from typing import SupportsIndex
from unittest.mock import patch
from urllib.parse import unquote

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import url_decoding
from zerver.lib.narrow_helpers import NarrowTerm
from zerver.lib.url_decoding import decode_hash_component, parse_narrow_url


class NarrowFragmentTextRustTest(SimpleTestCase):
    def test_decodes_both_ordinary_zulip_and_percent_formats(self) -> None:
        cases = {
            "": "",
            ".20": " ",
            "a+b": "a+b",
            ".29(hello.2Eworld)": ")(hello.world)",
            "اب.%32%35": "اب%25",
            "آرش.20.D8.B3%D9%84.D8.A7%D9%85": "آرش سلام",
            ".f0%9f.99%82": "🙂",
            ".252E": "%2E",
            "%00": "\0",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                with patch.object(url_decoding, "_rust_narrow_fragment_text", ModuleType("old")):
                    self.assertEqual(decode_hash_component(raw), expected)
                self.assertEqual(decode_hash_component(raw), expected)

    def test_malformed_and_surrogate_inputs_keep_urllib_behavior(self) -> None:
        for raw in (".", ".GG", "x..20", ".C3", ".F0.80", "%ED%A0%80", "\udfff.20", ".a", "%%29"):
            with self.subTest(raw=raw):
                self.assertEqual(decode_hash_component(raw), unquote(raw.replace(".", "%")))

        class CustomText(str):
            __slots__ = ()

            @override
            def replace(self, old: str, new: str, count: SupportsIndex = -1, /) -> str:
                return "replaced%20value"

        self.assertEqual(decode_hash_component(CustomText("raw")), "replaced value")

    def test_real_narrow_parser_keeps_topic_unicode_and_empty_topic(self) -> None:
        self.assertEqual(
            parse_narrow_url("#narrow/channel/9-test/topic/.20آرش.20%D8%AC.2E"),
            [NarrowTerm("channel", 9, False), NarrowTerm("topic", "آرش ج.", False)],
        )
        self.assertEqual(parse_narrow_url("#narrow/topic/"), [NarrowTerm("topic", "", False)])

    def test_native_dispatch_receives_only_the_extracted_fragment(self) -> None:
        native = url_decoding._rust_narrow_fragment_text
        if native is None:
            self.skipTest("requires the current Rust narrow text decoder")
        with patch.object(
            native, "decode_narrow_hash_component", wraps=native.decode_narrow_hash_component
        ) as call:
            self.assertEqual(decode_hash_component("Hello.20World"), "Hello World")
        call.assert_called_once_with("Hello.20World")
