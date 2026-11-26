from types import SimpleNamespace
from typing import cast
from unittest import TestCase
from unittest.mock import patch

from zerver.lib import url_encoding
from zerver.models import Realm, Stream


class PreparedChannelTopicLinksRustTest(TestCase):
    def test_channel_and_empty_topic_paths_are_distinct(self) -> None:
        realm = cast("Realm", SimpleNamespace(url="https://example.test"))
        stream = cast("Stream", SimpleNamespace(id=-4, name="A ."))
        base = "https://example.test/#narrow/channel/-4-A-.2E"
        self.assertEqual(url_encoding.stream_narrow_url(realm, stream), base)
        self.assertEqual(
            url_encoding.topic_narrow_url(realm=realm, stream=stream, topic_name=""),
            base + "/topic/",
        )
        with self.assertRaises(UnicodeEncodeError):
            url_encoding.topic_narrow_url(realm=realm, stream=stream, topic_name="\ud800")

    def test_actual_dispatch_and_preserved_pipeline_order(self) -> None:
        native = url_encoding._build_prepared_channel_topic_url
        if native is None:
            self.skipTest("requires the current native channel/topic link builder")
        realm = cast("Realm", SimpleNamespace(url="realm"))
        stream = cast("Stream", SimpleNamespace(id=1, name="unchanged"))
        calls: list[str] = []

        def channel(_id: int, _name: str) -> str:
            calls.append("channel")
            return "channel"

        def topic(name: str) -> str:
            calls.append(name)
            return "topic"

        with (
            patch.object(url_encoding, "_build_prepared_channel_topic_url", wraps=native) as render,
            patch.object(url_encoding, "encode_channel", channel),
            patch.object(url_encoding, "encode_hash_component", topic),
        ):
            self.assertEqual(
                url_encoding.topic_narrow_url(realm=realm, stream=stream, topic_name="original"),
                "realm/#narrow/channel/channel/topic/topic",
            )
            self.assertEqual(
                url_encoding.stream_narrow_url(realm, stream), "realm/#narrow/channel/channel"
            )
        self.assertEqual(calls, ["channel", "original", "channel"])
        self.assertEqual(render.call_count, 2)
