import random
from contextlib import nullcontext
from typing import cast
from unittest import TestCase, mock
from xml.etree.ElementTree import Element, tostring

from markdown.util import AtomicString

from zerver.lib import markdown
from zerver.lib.markdown import (
    StreamTopicMessageProcessor,
    StreamTopicPattern,
    ZulipMarkdown,
    channel_links,
)
from zerver.lib.topic_link_util import TOPIC_LINK_SYNTAX_FOR_DISPLAY


class MarkdownChannelLinkTest(TestCase):
    def render_link(
        self,
        channel_name: str,
        topic_name: str | None = None,
        message_id: str | None = None,
        *,
        channel_id: int | None = 42,
        with_operand: int | None = None,
        empty_topic_only: bool = False,
        use_python: bool = False,
    ) -> tuple[bytes | None, bool, bool, int | None, int | None]:
        engine = ZulipMarkdown(linkifiers=[], linkifiers_key=-1, email_gateway=False)
        name = "stream" if topic_name is None else "topic"
        if message_id is not None:
            name = "stream_topic_message"
        processor = cast(StreamTopicMessageProcessor, engine.inlinePatterns[name])
        match = mock.Mock()
        match.group.side_effect = {
            "stream_name": channel_name,
            "topic_name": topic_name,
            "message_id": message_id,
        }.__getitem__
        match.start.return_value = 7
        match.end.return_value = 23
        context = (
            mock.patch.object(channel_links, "_rust_channel_spans", None)
            if use_python
            else nullcontext()
        )
        with (
            context,
            mock.patch.object(
                StreamTopicMessageProcessor, "find_stream_id", return_value=channel_id
            ),
            mock.patch.object(
                StreamTopicMessageProcessor,
                "is_empty_topic_only_channel",
                return_value=empty_topic_only,
            ),
            mock.patch.object(StreamTopicPattern, "get_with_operand", return_value=with_operand),
        ):
            element, start, end = processor.handleMatch(match, "")
        if element is None:
            return None, False, False, start, end
        assert isinstance(element, Element)
        return (
            tostring(element),
            isinstance(element.text, AtomicString),
            bool(len(element)) and isinstance(element[0].tail, AtomicString),
            start,
            end,
        )

    def test_channel_topic_and_message_fields(self) -> None:
        self.assertEqual(TOPIC_LINK_SYNTAX_FOR_DISPLAY, "#{channel_name} > {topic_name}")
        result = self.render_link("dev tools", "a.b /📝", with_operand=99)
        self.assertEqual(
            result,
            (
                (
                    b'<a class="stream-topic" data-stream-id="42" '
                    b'href="/#narrow/channel/42-dev-tools/topic/a.2Eb.20.2F.F0.9F.93.9D/with/99">'
                    b"#dev tools &gt; a.b /&#128221;</a>"
                ),
                True,
                False,
                7,
                23,
            ),
        )
        result = self.render_link("name", "", "000123")
        self.assertEqual(
            result,
            (
                (
                    b'<a class="message-link" href="/#narrow/channel/42-name/topic//near/000123">'
                    b"#name &gt; <em>general chat</em> @ &#128172;</a>"
                ),
                True,
                True,
                7,
                23,
            ),
        )
        for name in ["عنوان📝", "a b.c/%", '<name & "value">', "**name**"]:
            for topic in [None, "", "موضوع **پررنگ** <&>"]:
                for message_id in [None, "000123"]:
                    if topic is None and message_id is not None:
                        continue
                    for anchor in [None, 0, 99, 2**100]:
                        with self.subTest(
                            name=name, topic=topic, message_id=message_id, anchor=anchor
                        ):
                            self.assertEqual(
                                self.render_link(name, topic, message_id, with_operand=anchor),
                                self.render_link(
                                    name, topic, message_id, with_operand=anchor, use_python=True
                                ),
                            )

    def test_generated_names(self) -> None:
        rng = random.Random(0)
        alphabet = "ab -.%/<>&\"'📝عنوان*\n\t\u0085"
        for _ in range(300):
            name = "".join(rng.choices(alphabet, k=rng.randrange(20)))
            topic = "".join(rng.choices(alphabet, k=rng.randrange(20)))
            for message_id in [None, "000" + str(rng.randrange(100000))]:
                with self.subTest(name=name, topic=topic, message_id=message_id):
                    self.assertEqual(
                        self.render_link(name, topic, message_id, with_operand=99),
                        self.render_link(name, topic, message_id, with_operand=99, use_python=True),
                    )

    def test_access_and_topic_policy_before_rust(self) -> None:
        with mock.patch.object(channel_links, "channel_link", side_effect=AssertionError):
            self.assertEqual(
                self.render_link("missing", channel_id=None), (None, False, False, None, None)
            )
            self.assertEqual(
                self.render_link("missing", "topic", channel_id=None),
                (None, False, False, None, None),
            )
            for message_id in [None, "123"]:
                self.assertEqual(
                    self.render_link("name", "topic", message_id, empty_topic_only=True),
                    (None, False, False, None, None),
                )
        for message_id in [None, "123"]:
            self.assertEqual(
                self.render_link("name", "", message_id, empty_topic_only=True),
                self.render_link("name", "", message_id, empty_topic_only=True, use_python=True),
            )

    def test_large_identifiers_and_surrogate_fallback(self) -> None:
        self.assertEqual(
            self.render_link("name", "topic", channel_id=2**100),
            self.render_link("name", "topic", channel_id=2**100, use_python=True),
        )
        for name, topic in [("\ud800", "topic"), ("name", "\udfff")]:
            for use_python in [False, True]:
                with (
                    self.subTest(name=name, topic=topic, use_python=use_python),
                    self.assertRaises(UnicodeEncodeError),
                ):
                    self.render_link(name, topic, use_python=use_python)

    def test_rust_path(self) -> None:
        if channel_links._rust_channel_spans is None:
            self.skipTest("Rust channel link fields are disabled")
        with (
            mock.patch.object(markdown, "encode_channel", side_effect=AssertionError),
            mock.patch.object(markdown, "encode_hash_component", side_effect=AssertionError),
        ):
            self.assertIsNotNone(self.render_link("name")[0])
            self.assertIsNotNone(self.render_link("name", "topic", with_operand=123)[0])
            self.assertIsNotNone(self.render_link("name", "", "000123")[0])
