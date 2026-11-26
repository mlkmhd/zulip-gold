from importlib import import_module
from random import Random
from types import ModuleType, SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

from django.db import connection
from django.test.utils import CaptureQueriesContext

from zerver.actions.message_edit import update_user_message_flags
from zerver.lib import native_message_edit_flags
from zerver.lib.markdown import MessageRenderingResult
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserMessage


def extension() -> ModuleType:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(backend, "plan_user_message_flag_deltas", None)):
        raise SkipTest("Rust extension does not support receipt edit flags")
    return backend


class MessageEditFlagsRustTest(ZulipTestCase):
    def test_rust_plans_exactly_the_rendering_bits(self) -> None:
        backend = extension()
        flags = UserMessage.flags
        masks = (
            flags.has_alert_word.mask,
            flags.mentioned.mask,
            flags.stream_wildcard_mentioned.mask,
            flags.topic_wildcard_mentioned.mask,
        )
        random = Random(9023)
        ids = random.sample(range(10000), 630)
        rows = [(user_id, random.getrandbits(64)) for user_id in ids]
        alerts = set(ids[::7])
        mentions = frozenset(ids[::5])
        topics = set(ids[::3])
        for stream_mention in (False, True):
            expected = []
            for index, (user_id, current) in enumerate(rows):
                target = current
                for enabled, flag in zip(
                    (user_id in alerts, user_id in mentions, stream_mention, user_id in topics),
                    masks,
                    strict=True,
                ):
                    target = target | flag if enabled else target & ~flag
                if target != current:
                    expected.append((index, current ^ target))
            self.assertEqual(
                backend.plan_user_message_flag_deltas(
                    rows, masks, (alerts, mentions, topics), stream_mention
                ),
                expected,
            )
        for malformed in ([(1, 0), (1, 4)], [(1, -1)], [(2**100, 0)]):
            self.assertIsNone(
                backend.plan_user_message_flag_deltas(
                    malformed, masks, (set(), set(), set()), False
                )
            )

    def test_exact_same_receipt_rows_are_saved_in_each_mode(self) -> None:
        backend = extension()
        owner = self.example_user("hamlet")
        peer = self.example_user("cordelia")
        other = self.example_user("othello")
        message_id = self.send_group_direct_message(owner, [peer, other], "Test flag receipt")
        selected = UserMessage.objects.filter(message_id=message_id).order_by("id")
        flags = UserMessage.flags
        old = {
            owner.id: flags.read | flags.starred | flags.stream_wildcard_mentioned,
            peer.id: flags.read | flags.is_private | flags.has_alert_word,
            other.id: flags.read | flags.is_private | flags.topic_wildcard_mentioned,
        }
        expected = {
            owner.id: flags.read | flags.starred | flags.mentioned | flags.topic_wildcard_mentioned,
            peer.id: old[peer.id],
            other.id: flags.read | flags.is_private,
        }
        rendered = MessageRenderingResult(
            rendered_content="",
            mentions_stream_wildcard=False,
            mentions_topic_wildcard=True,
            mentions_user_ids={owner.id},
            mentions_user_group_ids=set(),
            alert_words=set(),
            links_for_preview=set(),
            user_ids_with_alert_words={peer.id},
            potential_attachment_path_ids=[],
            thumbnail_spinners=set(),
            has_image=False,
            has_link=False,
        )
        witness = Mock(wraps=backend.plan_user_message_flag_deltas)
        decline = Mock(return_value=None)
        original_save = UserMessage.save
        saved_orders = []
        sql = []
        for candidate, prepared, keep_queryset in (
            (None, None, False),
            (SimpleNamespace(plan_user_message_flag_deltas=witness), None, False),
            (SimpleNamespace(), None, False),
            (SimpleNamespace(plan_user_message_flag_deltas=decline), None, False),
            (None, backend, True),
        ):
            for user_id, mask in old.items():
                selected.filter(user_profile_id=user_id).update(flags=mask)
            with (
                patch.object(native_message_edit_flags, "_backend", candidate),
                patch.object(native_message_edit_flags, "_prepared_backend", prepared),
                patch.object(backend, "plan_user_message_flag_deltas", witness),
                patch.object(UserMessage, "save", autospec=True, side_effect=original_save) as save,
                CaptureQueriesContext(connection) as queries,
            ):
                if prepared is not None:
                    self.assertFalse(native_message_edit_flags.available(iter(())))
                    self.assertFalse(native_message_edit_flags.available(selected.defer("flags")))
                    self.assertFalse(
                        native_message_edit_flags.available(selected.values_list("id"))
                    )
                source = selected.all() if keep_queryset else iter(selected.all())
                update_user_message_flags(rendered, source, {owner.id})
                saved_orders.append([call.args[0].user_profile_id for call in save.call_args_list])
                self.assertTrue(
                    all(call.kwargs == {"update_fields": ["flags"]} for call in save.call_args_list)
                )
            sql.append([query["sql"] for query in queries.captured_queries])
            self.assertEqual(
                {row.user_profile_id: int(row.flags) for row in selected.all()}, expected
            )
        self.assertCountEqual(saved_orders[0], [owner.id, other.id])
        self.assertEqual(saved_orders, [saved_orders[0]] * 5)
        self.assertEqual(sql, [sql[0]] * 5)
        self.assertEqual(witness.call_count, 2)
        decline.assert_called_once()
