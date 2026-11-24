import heapq
from importlib import import_module
from os import environ
from random import Random
from types import ModuleType, SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import digest
from zerver.lib.digest import DigestTopic, get_hot_topics

Row = tuple[int, int, int]


def extension() -> ModuleType:
    try:
        native = import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(native, "rank_eligible_digest_topic_indices", None)):
        raise SkipTest("Rust extension does not support digest topic ranking")
    return native


def reference(rows: list[Row]) -> list[int]:
    indices = range(len(rows))
    result = heapq.nlargest(2, indices, key=lambda index: rows[index][1])
    for index in heapq.nlargest(4, indices, key=lambda i: rows[i][2]):
        if all(rows[index][0] != rows[picked][0] for picked in result):
            result.append(index)
        if len(result) == 4:
            break
    return result


class DigestRankRustTest(SimpleTestCase):
    def test_order_identity_and_two_ranked_stages(self) -> None:
        native = extension()
        self.assertEqual(native.rank_eligible_digest_topic_indices([]), [])
        self.assertEqual(native.rank_eligible_digest_topic_indices([(8, 0, 0)]), [0])
        ties = [(label, 4, 9) for label in range(8)]
        self.assertEqual(native.rank_eligible_digest_topic_indices(ties), [0, 1, 2, 3])
        aliases = [(1, 6, 9), (1, 6, 9), (2, 5, 8), (2, 5, 8), (3, 1, 7), (4, 1, 6)]
        self.assertEqual(native.rank_eligible_digest_topic_indices(aliases), [0, 1, 2])

        rng = Random(15189)
        for _ in range(500):
            pool = [
                (identity, rng.randrange(10), rng.randrange(16))
                for identity in range(rng.randrange(1, 14))
            ]
            rows = rng.choices(pool, k=rng.randrange(48))
            self.assertEqual(native.rank_eligible_digest_topic_indices(rows), reference(rows))

    def test_noncanonical_rows_decline(self) -> None:
        native = extension()
        baseline = [(1, 2, 3)]
        for extra in (
            (1, 3, 3),
            (2, True, 3),
            (2, -1, 3),
            (2**100, 2, 3),
            (2, 3),
            [2, 3, 4],
        ):
            self.assertIsNone(native.rank_eligible_digest_topic_indices([*baseline, extra]))
        self.assertIsNone(native.rank_eligible_digest_topic_indices(tuple(baseline)))
        self.assertEqual(baseline, [(1, 2, 3)])


def topic(stream_id: int, diversity: int, count: int) -> DigestTopic:
    result = DigestTopic((stream_id, "synthetic repeated key"))
    result.human_senders = {str(index) for index in range(diversity)}
    result.num_human_messages = count
    return result


class DigestRankDjangoDispatchRustTest(SimpleTestCase):
    def test_uses_current_extension_only_when_enabled_after_stream_filter(self) -> None:
        native = extension()
        aliased = topic(1, 6, 9)
        twin = topic(1, 5, 8)
        next_topic = topic(1, 1, 7)
        unselected = topic(2, 100, 100)
        inputs = [aliased, unselected, aliased, twin, twin, next_topic, topic(1, 1, 6)]

        with patch.object(
            native,
            "rank_eligible_digest_topic_indices",
            wraps=native.rank_eligible_digest_topic_indices,
        ) as call:
            result = get_hot_topics(inputs, {1})
        self.assertEqual(result, [aliased, aliased, twin])
        self.assertEqual(aliased.topic_key, twin.topic_key)
        self.assertIs(result[0], result[1])
        self.assertIs(result[2], twin)
        if environ.get("ZULIP_RUST_DIGEST_RANK") == "1":
            call.assert_called_once_with(
                [
                    (id(aliased), 6, 9),
                    (id(aliased), 6, 9),
                    (id(twin), 5, 8),
                    (id(twin), 5, 8),
                    (id(next_topic), 1, 7),
                    (id(inputs[-1]), 1, 6),
                ]
            )
        else:
            call.assert_not_called()

    def test_consumes_native_positions_and_empty_list(self) -> None:
        first = topic(1, 5, 8)
        outside = topic(2, 6, 9)
        second = topic(1, 1, 7)
        rank = Mock(side_effect=[[1, 0], []])
        with patch.object(
            digest, "_rust_digest_rank", SimpleNamespace(rank_eligible_digest_topic_indices=rank)
        ):
            self.assertEqual(get_hot_topics([first, outside, second], {1}), [second, first])
            self.assertEqual(get_hot_topics([first, second], {1}), [])
        self.assertEqual(rank.call_count, 2)

    def test_python_fallback_for_absent_old_declined_and_unsupported_scores(self) -> None:
        first = topic(1, 9, 8)
        second = topic(1, 8, 7)
        huge = topic(1, 0, 2**80)
        later = topic(1, 0, 9)
        inputs = [first, second, huge, later]
        decline = Mock(return_value=None)
        native = extension()
        for backend in (
            None,
            SimpleNamespace(),
            SimpleNamespace(rank_eligible_digest_topic_indices=decline),
            native,
        ):
            with patch.object(digest, "_rust_digest_rank", backend):
                self.assertEqual(get_hot_topics(inputs, {1}), [first, second, huge, later])
        decline.assert_called_once()

    def test_different_limits_malformed_scores_and_equality_remain_python(self) -> None:
        class EqualTopic(DigestTopic):
            @override
            def __eq__(self, other: object) -> bool:
                return isinstance(other, EqualTopic) and self.topic_key == other.topic_key

            @override
            def __hash__(self) -> int:
                return hash(self.topic_key)

        first = topic(1, 9, 8)
        second = topic(1, 8, 7)
        unusual_first = EqualTopic(first.topic_key)
        unusual_first.human_senders = first.human_senders
        unusual_first.num_human_messages = first.num_human_messages
        custom_equal = EqualTopic(first.topic_key)
        custom_equal.num_human_messages = 20
        fourth = topic(1, 0, 15)
        fifth = topic(1, 0, 10)
        rank = Mock()
        with patch.object(
            digest, "_rust_digest_rank", SimpleNamespace(rank_eligible_digest_topic_indices=rank)
        ):
            with patch.object(digest, "MAX_HOT_TOPICS_TO_BE_INCLUDED_IN_DIGEST", 3):
                self.assertEqual(
                    get_hot_topics([first, second, fourth, fifth], {1}), [first, second, fourth]
                )
            with (
                patch.object(digest, "MAX_HOT_TOPICS_TO_BE_INCLUDED_IN_DIGEST", 4.0),
                self.assertRaises(TypeError),
            ):
                get_hot_topics([first, second], {1})

            selected = get_hot_topics([unusual_first, second, custom_equal, fourth, fifth], {1})
            self.assertEqual(
                [id(item) for item in selected],
                [id(unusual_first), id(second), id(fourth), id(fifth)],
            )
            with patch.object(DigestTopic, "length", return_value=3):
                self.assertEqual(get_hot_topics([first, second], {1}), [first, second])

            with patch.object(DigestTopic, "__eq__", return_value=True):
                selected = get_hot_topics([first, second, fourth, fifth], {1})
                self.assertEqual([id(item) for item in selected], [id(first), id(second)])

            with (
                patch.object(first, "num_human_messages", "invalid"),
                self.assertRaises(TypeError),
            ):
                get_hot_topics([first, second], {1})
        rank.assert_not_called()
