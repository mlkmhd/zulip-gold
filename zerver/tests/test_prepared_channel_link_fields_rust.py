from unittest.mock import patch

from django.test import SimpleTestCase
from markdown.util import AtomicString

from zerver.lib import markdown, url_encoding
from zerver.lib.markdown import channel_links


class PreparedChannelLinkFieldsRustTest(SimpleTestCase):
    def test_standard_field_builder_preserves_existing_markup_shape(self) -> None:
        backend = channel_links._prepared_backend
        if backend is None:
            self.skipTest("Prepared channel link field extension is unavailable")
        with (
            patch.object(channel_links, "_rust_channel_spans", None),
            patch.object(
                backend, "markdown_channel_link_fields", wraps=backend.markdown_channel_link_fields
            ) as native,
        ):
            channel = channel_links.channel_link(64, "Ops & UI")
            topic = channel_links.channel_link(64, "Ops & UI", topic_name="آزمون", with_operand=7)
            message = channel_links.channel_link(
                64, "Ops & UI", topic_name="", message_id="۰۰۹", empty_topic_name="General chat"
            )
        assert channel is not None and topic is not None and message is not None
        self.assertEqual(
            list(channel.attrib.items()),
            [
                ("class", "stream"),
                ("data-stream-id", "64"),
                ("href", "/#narrow/channel/64-Ops-.26-UI"),
            ],
        )
        self.assertTrue(topic.attrib["href"].endswith("/with/7"))
        self.assertTrue(message.attrib["href"].endswith("/topic//near/۰۰۹"))
        self.assertEqual(message.attrib["class"], "message-link")
        self.assertEqual(message.text, "#Ops & UI > ")
        self.assertIs(type(message.text), AtomicString)
        self.assertEqual(
            [(node.tag, node.text, node.tail) for node in message],
            [("em", "General chat", " @ 💬")],
        )
        self.assertIs(type(message[0].tail), AtomicString)
        self.assertEqual(native.call_count, 3)

    def test_custom_encoding_and_noncanonical_shapes_remain_python(self) -> None:
        with patch.object(channel_links, "_rust_channel_spans", None):
            with patch.object(markdown, "encode_channel", return_value="custom"):
                self.assertIsNone(channel_links.channel_link(64, "Ops"))
            with patch.object(url_encoding, "hash_replacements", {"%": "_"}):
                self.assertIsNone(channel_links.channel_link(64, "Ops"))
            self.assertIsNone(channel_links.channel_link(2**100, "Ops"))
            self.assertIsNone(channel_links.channel_link(64, "surrogate\ud800"))
            self.assertIsNone(channel_links.channel_link(64, "Ops", message_id="2"))
