from contextlib import nullcontext
from random import Random
from unittest.mock import patch

import orjson
from django.test import SimpleTestCase

from zerver.lib import native_unread
from zerver.lib.event_types import MessageDetails
from zerver.lib.message import (
    RawUnreadMessagesResult,
    aggregate_unread_data,
    format_unread_message_details,
)
from zerver.models import Message


class UnreadAggregateRustTest(SimpleTestCase):
    def test_prepared_unread_payload_matches_byte_order(self) -> None:
        random = Random(8108)
        fallback = Message.EMPTY_TOPIC_FALLBACK_NAME
        names = ("", fallback.upper(), "ΑΟΣ", "αος", "İ", "i\u0307", "K", "k", "سلام")
        backend = native_unread._aggregate_backend
        witness = (
            patch.object(
                backend, "aggregate_unread_payload", wraps=backend.aggregate_unread_payload
            )
            if backend is not None
            else nullcontext()
        )
        with witness as native:
            for trial in range(220):
                ids = random.sample(range(1, 500), 24)
                raw: RawUnreadMessagesResult = {
                    "stream_dict": {
                        mid: {"stream_id": random.randrange(2, 5), "topic": random.choice(names)}
                        for mid in ids[:16]
                    },
                    "pm_dict": {mid: {"other_user_id": mid % 3} for mid in ids[16:21]},
                    "huddle_dict": {
                        mid: {"user_ids_string": ("1,2,23" if mid % 3 else "1,2,3")}
                        for mid in ids[21:]
                    },
                    "unmuted_stream_msgs": set(ids[1:5]),
                    "mentions": set(ids[::3]),
                    "old_unreads_missing": trial % 5 == 0,
                }
                for modern in (False, True):
                    with (
                        patch.object(native_unread, "_aggregate_backend", None),
                        patch.object(native_unread, "_backend", None),
                    ):
                        expected = aggregate_unread_data(raw, modern)
                    actual = aggregate_unread_data(raw, modern)
                    self.assertEqual(orjson.dumps(actual), orjson.dumps(expected))
            if native is not None:
                self.assertEqual(native.call_count, 440)

    def test_custom_topic_falls_back_to_its_original_comparison(self) -> None:
        class DerivedTopic(str):
            __slots__ = ()

        raw: RawUnreadMessagesResult = {
            "stream_dict": {3: {"stream_id": 10, "topic": DerivedTopic("Greek Σ")}},
            "pm_dict": {},
            "huddle_dict": {},
            "unmuted_stream_msgs": {3},
            "mentions": {3},
            "old_unreads_missing": False,
        }
        result = aggregate_unread_data(raw, True)
        self.assertIs(result["streams"][0]["topic"], raw["stream_dict"][3]["topic"])
        backend = native_unread._aggregate_backend
        if backend is not None:
            self.assertIsNone(
                backend.aggregate_unread_payload(raw, True, Message.EMPTY_TOPIC_FALLBACK_NAME)
            )


class UnreadDetailsRustTest(SimpleTestCase):
    def test_prepared_details_match_fields_and_insertion_order(self) -> None:
        raw: RawUnreadMessagesResult = {
            "pm_dict": {15: {"other_user_id": 4}, 3: {"other_user_id": 7}},
            "stream_dict": {
                8: {"stream_id": 9, "topic": "سلام"},
                2: {"stream_id": 9, "topic": ""},
            },
            "huddle_dict": {5: {"user_ids_string": "9,4,5,5"}},
            "unmuted_stream_msgs": {2},
            "mentions": {8, 5, 15},
            "old_unreads_missing": False,
        }
        with (
            patch.object(native_unread, "_details_backend", None),
            patch.object(native_unread, "_backend", None),
        ):
            expected = format_unread_message_details(4, raw)
        backend = native_unread._details_backend
        witness = (
            patch.object(backend, "format_unread_details", wraps=backend.format_unread_details)
            if backend is not None
            else nullcontext()
        )
        with witness as native:
            result = format_unread_message_details(4, raw)
        self.assertEqual(
            [(key, row.model_dump()) for key, row in result.items()],
            [(key, row.model_dump()) for key, row in expected.items()],
        )
        self.assertEqual(list(result), ["15", "3", "8", "2", "5"])
        self.assertEqual(result["15"].user_ids, [])
        self.assertEqual(result["5"].user_ids, [5, 5, 9])
        self.assertTrue(result["2"].unmuted_stream_msg)
        self.assertTrue(result["8"].mentioned)
        if native is not None:
            native.assert_called_once_with(4, raw, MessageDetails)

        # Python has historically accepted whitespace and a leading plus sign.
        raw["huddle_dict"][5]["user_ids_string"] = "+9, 4,5,5"
        if backend is not None:
            self.assertIsNone(backend.format_unread_details(4, raw, MessageDetails))
        self.assertEqual(format_unread_message_details(4, raw), result)
