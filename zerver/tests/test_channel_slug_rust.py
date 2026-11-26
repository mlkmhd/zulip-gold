from types import ModuleType
from typing import SupportsIndex
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import url_encoding
from zerver.lib.url_encoding import encode_channel, stream_message_url


class ChannelSlugDjangoRustTest(SimpleTestCase):
    def test_matches_legacy_slug_with_whitespace_escaped_chars_and_ids(self) -> None:
        names = ["", "A  B", "A\u00a0B", "Z %./()", "سال نو 🙂", "one\tline", "\0"]
        ids = [-(2**63), -1, 0, 9, 2**63 - 1, 2**80]
        combinations = [
            (pk, name, operator) for pk in ids for name in names for operator in (False, True)
        ]
        with patch.object(url_encoding, "_rust_channel_narrow_slug", ModuleType("old")):
            expected = [encode_channel(*parts) for parts in combinations]
        self.assertEqual([encode_channel(*parts) for parts in combinations], expected)
        self.assertEqual(encode_channel(9, "A  B\u00a0.", True), "channel/9-A--B.C2.A0.2E")

    def test_lone_surrogates_and_subclasses_preserve_original_behavior(self) -> None:
        class CustomName(str):
            __slots__ = ()

            @override
            def replace(self, old: str, new: str, count: SupportsIndex = -1, /) -> str:
                return "changed/name"

        class CustomID(int):
            @override
            def __str__(self) -> str:
                return "custom"

        self.assertEqual(encode_channel(CustomID(9), CustomName("A B")), "custom-changed.2Fname")
        with self.assertRaises(UnicodeEncodeError) as error:
            encode_channel(9, "a \udfff")
        with (
            patch.object(url_encoding, "_rust_channel_narrow_slug", None),
            self.assertRaises(UnicodeEncodeError) as ref,
        ):
            encode_channel(9, "a \udfff")
        self.assertEqual(error.exception.args, ref.exception.args)

    def test_existing_bare_id_link_does_not_consult_the_label_formatter(self) -> None:
        disabled = Mock(side_effect=AssertionError("bare-ID link must not format the channel name"))
        message = {
            "id": 123,
            "stream_id": 9,
            "display_recipient": "Not part of the link",
            "topic": "A B",
        }
        with patch.object(url_encoding, "encode_channel", disabled):
            self.assertEqual(
                stream_message_url(
                    None, message, conversation_link=True, include_channel_name=False
                ),
                "#narrow/channel/9/topic/A.20B/with/123",
            )
        disabled.assert_not_called()

    def test_verified_extension_handles_the_actual_channel_message_link(self) -> None:
        native = url_encoding._rust_channel_narrow_slug
        if native is None:
            self.skipTest("requires the current native channel slug contract")
        message = {"id": 123, "stream_id": 9, "display_recipient": "A B", "topic": "five.ten"}
        with patch.object(
            native, "encode_narrow_channel", wraps=native.encode_narrow_channel
        ) as call:
            self.assertEqual(
                stream_message_url(None, message), "#narrow/channel/9-A-B/topic/five.2Eten/near/123"
            )
        call.assert_called_once_with(9, "A B", False)
