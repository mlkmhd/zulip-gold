from importlib import import_module
from random import Random
from types import ModuleType, SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

from zerver.actions.message_send import create_user_messages
from zerver.lib import native_message_send_masks
from zerver.lib.markdown import MessageRenderingResult
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, UserMessage


def extension() -> ModuleType:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(backend, "plan_new_user_message_masks", None)):
        raise SkipTest("Rust extension does not support new receipt masks")
    return backend


class MessageSendMasksRustTest(ZulipTestCase):
    def test_ordered_mask_plan_and_no_unread_limit(self) -> None:
        backend = extension()
        flags = UserMessage.flags
        random = Random(1792)
        ordered = random.sample(range(10000), 621)
        marks = set(ordered[::7])
        mentions = set(ordered[::11])
        alerts = frozenset(ordered[::13])
        topics = set(ordered[::17])
        with patch.object(native_message_send_masks, "_backend", backend):
            for limit in (None, set(), set(ordered[::5])):
                reference = []
                for user_id in ordered:
                    expected = flags.is_private.mask
                    if user_id in marks or (limit is not None and user_id not in limit):
                        expected |= flags.read.mask
                    if user_id in mentions:
                        expected |= flags.mentioned.mask
                    if user_id in alerts:
                        expected |= flags.has_alert_word.mask
                    if user_id in topics:
                        expected |= flags.topic_wildcard_mentioned.mask
                    reference.append(expected)
                self.assertEqual(
                    native_message_send_masks.plan(
                        ordered, flags.is_private.mask, marks, mentions, alerts, topics, limit
                    ),
                    reference,
                )
            self.assertIsNone(
                native_message_send_masks.plan(
                    [ordered[0], 2**100], 0, marks, mentions, alerts, topics, None
                )
            )

    def test_same_initial_rows_flags_order_and_lazy_omission(self) -> None:
        backend = extension()
        flags = UserMessage.flags
        owner = self.example_user("hamlet")
        message = Message.objects.get(id=self.send_stream_message(owner, "Verona", "Test masks"))
        omitted, push, email, topic_push, topic_email, mentioned, topic, alerted, read = range(
            100, 109
        )
        eligible = set(range(100, 109))
        rendered = MessageRenderingResult(
            rendered_content="",
            mentions_stream_wildcard=False,
            mentions_topic_wildcard=True,
            mentions_user_ids={mentioned},
            mentions_user_group_ids=set(),
            alert_words=set(),
            links_for_preview=set(),
            user_ids_with_alert_words={alerted},
            potential_attachment_path_ids=[],
            thumbnail_spinners=set(),
            has_image=False,
            has_link=False,
        )
        witness = Mock(wraps=backend.plan_new_user_message_masks)
        decline = Mock(return_value=None)

        def receipt_rows() -> list[tuple[int, int, int]]:
            return [
                (row.user_profile_id, row.message_id, int(row.flags))
                for row in create_user_messages(
                    message,
                    rendered,
                    eligible,
                    eligible,
                    {push},
                    {email},
                    {mentioned},
                    {topic_push},
                    {topic_email},
                    {read},
                    None,
                    {topic},
                )
            ]

        with (
            patch.object(native_message_send_masks, "_backend", None),
            patch.object(native_message_send_masks, "_prepared_backend", None),
        ):
            reference = receipt_rows()
        self.assertEqual([row[0] for row in reference], [i for i in eligible if i != omitted])
        self.assertEqual(
            {user_id: mask for user_id, _, mask in reference},
            {
                push: 0,
                email: 0,
                topic_push: 0,
                topic_email: 0,
                mentioned: flags.mentioned.mask,
                topic: flags.topic_wildcard_mentioned.mask,
                alerted: flags.has_alert_word.mask,
                read: flags.read.mask,
            },
        )
        for candidate in (
            SimpleNamespace(plan_new_user_message_masks=witness),
            SimpleNamespace(),
            SimpleNamespace(plan_new_user_message_masks=decline),
        ):
            with (
                patch.object(native_message_send_masks, "_backend", candidate),
                patch.object(native_message_send_masks, "_prepared_backend", None),
            ):
                self.assertEqual(receipt_rows(), reference)
        with (
            patch.object(native_message_send_masks, "_backend", None),
            patch.object(
                native_message_send_masks,
                "_prepared_backend",
                SimpleNamespace(plan_new_user_message_masks=witness),
            ),
        ):
            self.assertEqual(receipt_rows(), reference)
            self.assertIsNone(
                native_message_send_masks.plan([100], -1, set(), set(), set(), set(), None)
            )
        self.assertEqual(witness.call_count, 2)
        decline.assert_called_once()
