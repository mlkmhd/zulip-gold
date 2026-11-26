from math import copysign
from random import Random
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.worker import user_activity


class ActivityBatchRustTest(SimpleTestCase):
    def test_native_grouping_keeps_first_seen_order_and_latest_timestamp(self) -> None:
        backend = user_activity._activity_backend
        if backend is None:
            self.skipTest("No prepared activity-worker ABI")
        rng = Random(68423)
        events = [
            dict(
                user_profile_id=rng.randrange(10),
                client_id=rng.randrange(4),
                query=rng.choice(("view", "update", "فارسی")),
                time=float(rng.randrange(-20, 20)),
            )
            for _ in range(1200)
        ]
        events[0:0] = [
            dict(user_profile_id=99, client_id=1, query="tie", time=-0.0),
            dict(user_profile_id=99, client_id=1, query="tie", time=0.0),
        ]
        with patch.object(user_activity, "_activity_backend", None):
            expected = user_activity._activity_rows(events)
        with patch.object(
            backend, "group_prepared_user_activity", wraps=backend.group_prepared_user_activity
        ) as witness:
            actual = user_activity._activity_rows(events)
        witness.assert_called_once_with(events)
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0][:4], (99, 1, "tie", 2))
        self.assertEqual(copysign(1.0, actual[0][4]), -1.0)

    def test_prepared_worker_declines_nonstandard_payloads(self) -> None:
        backend = user_activity._activity_backend
        if backend is None:
            self.skipTest("No prepared activity-worker ABI")
        valid = dict(user_profile_id=1, client_id=2, query="view", time=3.0)
        for event in [
            dict(valid, time=4),
            dict(valid, time=float("nan")),
            dict(valid, query=chr(0xD800)),
            dict(valid, user_profile_id=2**70),
            {},
        ]:
            self.assertIsNone(backend.group_prepared_user_activity([valid, event]))
        with self.assertRaisesRegex(KeyError, "user_profile_id"):
            user_activity._activity_rows([valid, {}])
