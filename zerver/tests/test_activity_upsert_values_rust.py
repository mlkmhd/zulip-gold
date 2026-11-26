from datetime import datetime
from unittest.mock import patch

from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserActivity
from zerver.models.clients import get_client
from zerver.worker import user_activity


class ActivityUpsertValuesRustTest(ZulipTestCase):
    def test_worker_keeps_exact_upsert_and_persisted_rows(self) -> None:
        backend = user_activity._activity_upsert_backend
        if backend is None or not hasattr(backend, "plan_prepared_activity_values"):
            self.skipTest("No activity UPSERT values ABI")
        user = self.example_user("hamlet")
        client = get_client("ios")
        events = [
            dict(
                user_profile_id=user.id,
                client_id=client.id,
                query=query,
                time=100.5 + index,
            )
            for index, query in enumerate(("view", "writer's page", "view", "فارسی"))
        ]

        def run_worker() -> tuple[list[str], list[tuple[str, int, datetime]]]:
            with transaction.atomic():
                worker = user_activity.UserActivityWorker()
                with CaptureQueriesContext(connection) as capture:
                    worker.consume_batch(events)
                records = list(
                    UserActivity.objects.filter(user_profile=user, client=client)
                    .order_by("query")
                    .values_list("query", "count", "last_visit")
                )
                transaction.set_rollback(True)
            return [entry["sql"] for entry in capture], records

        with patch.object(user_activity, "_activity_upsert_backend", None):
            expected = run_worker()
        with patch.object(
            backend, "plan_prepared_activity_values", wraps=backend.plan_prepared_activity_values
        ) as plan:
            actual = run_worker()
        plan.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual[0]), 1)
        self.assertEqual(len(actual[1]), 3)
        placeholders, parameters = backend.plan_prepared_activity_values(
            [(user.id, client.id, "writer's page", 1, 99.0)]
        )
        self.assertEqual(placeholders, "(%s,%s,%s,%s,to_timestamp(%s))")
        self.assertEqual(parameters, (user.id, client.id, "writer's page", 1, 99.0))
        self.assertIsNone(backend.plan_prepared_activity_values([]))
