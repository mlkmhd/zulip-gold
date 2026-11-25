import os
from typing import cast
from unittest.mock import Mock, patch

import orjson
from typing_extensions import override

from zerver.lib import url_encoding
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.url_encoding import (
    encode_channel,
    encode_hash_component,
    encode_user_full_name_and_id,
    encode_user_ids,
    message_link_url,
    stream_message_url,
)
from zerver.models.messages import Message
from zerver.models.realms import get_realm
from zerver.models.streams import get_stream


class URLEncodeTest(ZulipTestCase):
    def test_encode_hash_component(self) -> None:
        fixture_path = os.path.join(
            os.path.dirname(__file__), "fixtures/url_encoding_test_cases.json"
        )
        with open(fixture_path, "rb") as f:
            data = orjson.loads(f.read())
        for test in data:
            with self.subTest(name=test["name"]):
                self.assertEqual(encode_hash_component(test["input"]), test["expected_output"])

    def test_prepared_fragments_match_the_original_encoder(self) -> None:
        backend = url_encoding._rust_url_fragments
        samples = ["", "".join(map(chr, range(256))), "سلا‌م (a.b) / 🙂", "\u2028", "a\0b"]
        ids = [[1], [4, 10, 7, 10], [-(2**63), 2**63 - 1], [2**63, 1]]
        with patch.object(url_encoding, "_rust_url_fragments", None):
            expected_text = [encode_hash_component(value) for value in samples]
            expected_ids = [encode_user_ids(group, with_operator=True) for group in ids]
        spy = Mock(wraps=backend) if backend is not None else None
        with patch.object(url_encoding, "_rust_url_fragments", spy):
            self.assertEqual([encode_hash_component(value) for value in samples], expected_text)
            self.assertEqual(
                [encode_user_ids(group, with_operator=True) for group in ids], expected_ids
            )
        if spy is not None:
            self.assertEqual(
                [call.args for call in spy.encode_narrow_hash_component.call_args_list],
                [(value,) for value in samples],
            )
            self.assertEqual(
                [call.args for call in spy.encode_narrow_user_ids.call_args_list],
                [(group, True) for group in ids],
            )

    def test_fragment_special_inputs_keep_python_semantics(self) -> None:
        class CustomEncoding(str):
            __slots__ = ()

            @override
            def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
                return b"original/encoder"

        class CustomOperator:
            def __bool__(self) -> bool:
                return True

        backend = Mock()
        with patch.object(url_encoding, "_rust_url_fragments", backend):
            self.assertEqual(encode_hash_component(CustomEncoding("value")), "original.2Fencoder")
            self.assertEqual(encode_user_ids([2, 1], cast("bool", CustomOperator())), "dm/1,2")
        backend.encode_narrow_hash_component.assert_not_called()
        backend.encode_narrow_user_ids.assert_not_called()
        for value in ("\ud800", "a\udfff", "🙂\ud800\udfff"):
            with self.assertRaises(UnicodeEncodeError) as error:
                encode_hash_component(value)
            with (
                patch.object(url_encoding, "_rust_url_fragments", None),
                self.assertRaises(UnicodeEncodeError) as reference,
            ):
                encode_hash_component(value)
            self.assertEqual(error.exception.args, reference.exception.args)

    def test_encode_channel(self) -> None:
        # We have more tests for this function in `test_topic_link_utils.py`
        self.assertEqual(encode_channel(9, "Verona"), "9-Verona")
        self.assertEqual(encode_channel(123, "General"), "123-General")
        self.assertEqual(encode_channel(7, "random_channel"), "7-random_channel")
        self.assertEqual(encode_channel(9, "Verona", with_operator=True), "channel/9-Verona")

    def test_encode_user_ids(self) -> None:
        # Group narrow URL has 3 or more user IDs
        self.assertEqual(encode_user_ids([1, 2, 3]), "1,2,3-group")
        self.assertEqual(encode_user_ids([3, 1, 2]), "1,2,3-group")
        self.assertEqual(encode_user_ids([3, 1, 1]), "1,1,3-group")
        self.assertEqual(encode_user_ids([2**63, 1]), "1,9223372036854775808")

        # One-on-one narrow URL has 2 user IDs
        self.assertEqual(encode_user_ids([1, 2]), "1,2")

        # Narrow URL to ones own direct message conversation
        self.assertEqual(encode_user_ids([1]), "1")

        self.assertEqual(encode_user_ids([1, 2, 3], with_operator=True), "dm/1,2,3-group")
        with self.assertRaises(AssertionError):
            encode_user_ids([])

    def test_encode_user_full_name_and_id(self) -> None:
        self.assertEqual(encode_user_full_name_and_id("King Hamlet", 9), "9-King-Hamlet")
        self.assertEqual(
            encode_user_full_name_and_id("King Hamlet", 9, with_operator=True), "dm/9-King-Hamlet"
        )
        self.assertEqual(encode_user_full_name_and_id("ZOE", 1), "1-ZOE")
        self.assertEqual(encode_user_full_name_and_id("  User Name  ", 100), "100-User-Name")
        self.assertEqual(encode_user_full_name_and_id("User  Name", 101), "101-User-Name")
        self.assertEqual(encode_user_full_name_and_id("User/Name", 200), "200-User-Name")
        self.assertEqual(encode_user_full_name_and_id("User%Name", 201), "201-User-Name")
        self.assertEqual(encode_user_full_name_and_id("User<Name>", 202), "202-User-Name-")
        self.assertEqual(encode_user_full_name_and_id('User"Name`', 203), "203-User-Name-")
        self.assertEqual(encode_user_full_name_and_id('User/ % < > ` " Name', 204), "204-User-Name")
        self.assertEqual(encode_user_full_name_and_id("User--Name", 205), "205-User--Name")
        self.assertEqual(encode_user_full_name_and_id("User%%Name", 206), "206-User-Name")
        self.assertEqual(encode_user_full_name_and_id("User_Name", 5), "5-User_Name")

    def test_stream_message_url(self) -> None:
        realm = get_realm("zulip")
        topic = "test topic"
        channel = get_stream("Verona", realm)
        channel_message_id = self.send_stream_message(
            sender=self.example_user("hamlet"), stream_name=channel.name, topic_name=topic
        )
        channel_message = Message.objects.get(id=channel_message_id, realm=realm)
        message_dict = dict(
            id=channel_message_id,
            stream_id=channel.id,
            display_recipient=channel_message.recipient.label(),
            topic=topic,
        )
        channel_message_url = stream_message_url(
            realm,
            message_dict,
        )
        expected_channel_message_url = f"{realm.url}/#narrow/{encode_channel(channel.id, channel.name, True)}/topic/{encode_hash_component(topic)}/near/{channel_message_id}"
        self.assertEqual(channel_message_url, expected_channel_message_url)

        relative_channel_message_url = stream_message_url(None, message_dict)
        expected_relative_channel_message_url = f"#narrow/{encode_channel(channel.id, channel.name, True)}/topic/{encode_hash_component(topic)}/near/{channel_message_id}"
        self.assertEqual(relative_channel_message_url, expected_relative_channel_message_url)

    def test_message_link_url_without_a_realm(self) -> None:
        realm = get_realm("zulip")
        channel_message = dict(
            type="stream",
            id=555,
            stream_id=9,
            display_recipient="Verona",
            topic="test topic",
        )
        direct_message = dict(
            type="personal",
            id=556,
            display_recipient=[dict(id=77), dict(id=80)],
        )

        self.assertEqual(
            message_link_url(realm, channel_message),
            f"{realm.url}/#narrow/channel/9-Verona/topic/test.20topic/near/555",
        )
        self.assertEqual(
            message_link_url(None, channel_message),
            "#narrow/channel/9-Verona/topic/test.20topic/near/555",
        )

        self.assertEqual(
            message_link_url(realm, direct_message),
            f"{realm.url}/#narrow/dm/77,80/near/556",
        )
        self.assertEqual(
            message_link_url(None, direct_message),
            "#narrow/dm/77,80/near/556",
        )
