from datetime import datetime, timedelta, timezone
from itertools import product
from unittest.mock import patch

from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from zerver.actions import user_activity
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserActivityInterval


class ActivityIntervalRustTest(ZulipTestCase):
    def test_native_interval_uses_exact_utc_microseconds_and_tie_identity(self) -> None:
        backend = user_activity._interval_backend
        if backend is None or not hasattr(backend, "plan_prepared_activity_interval"):
            self.skipTest("No prepared activity-interval ABI")
        moment = datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
        samples = range(-2, 3)
        for offsets in product(samples, repeat=4):
            points = tuple(moment + timedelta(microseconds=offset) for offset in offsets)
            old_start, old_end, new_start, new_end = points
            with patch.object(user_activity, "_interval_backend", None):
                expected = user_activity._plan_activity_interval(
                    old_start, old_end, new_start, new_end
                )
            actual = backend.plan_prepared_activity_interval(*points, (datetime, timezone.utc))
            self.assertIsNotNone(actual)
            self.assertEqual(actual[0], expected[0])
            self.assertIs(actual[1], expected[1])
            self.assertIs(actual[2], expected[2])
        self.assertIsNone(
            backend.plan_prepared_activity_interval(
                moment.replace(tzinfo=None), moment, moment, moment, (datetime, timezone.utc)
            )
        )

    def test_activity_interval_persists_touching_and_disjoint_records(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        length = UserActivityInterval.MIN_INTERVAL_LENGTH
        user = self.example_user("hamlet")

        def run() -> tuple[list[tuple[datetime, datetime]], list[str]]:
            with transaction.atomic():
                with CaptureQueriesContext(connection) as queries:
                    for elapsed in (timedelta(), length, length * 2 + timedelta(microseconds=1)):
                        user_activity.do_update_user_activity_interval(user, start + elapsed)
                records = list(
                    UserActivityInterval.objects.filter(user_profile=user)
                    .order_by("start")
                    .values_list("start", "end")
                )
                transaction.set_rollback(True)
            return records, [query["sql"].split(" ", 1)[0] for query in queries]

        with patch.object(user_activity, "_interval_backend", None):
            expected = run()
        actual = run()
        self.assertEqual(actual, expected)
        epsilon = timedelta(microseconds=1)
        self.assertEqual(
            actual,
            (
                [
                    (start, start + length * 2),
                    (start + length * 2 + epsilon, start + length * 3 + epsilon),
                ],
                ["SELECT", "INSERT", "SELECT", "UPDATE", "SELECT", "INSERT"],
            ),
        )
