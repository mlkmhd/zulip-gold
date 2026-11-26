from typing import Any
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from zerver.lib import queue


class QueueRetryRustTest(SimpleTestCase):
    def test_original_event_goes_to_exactly_one_callback(self) -> None:
        original = {"type": "synthetic", "data": {"same": True}}
        for attempts, expected, terminal in [
            (None, 1, False),
            (-1, 0, False),
            (2, 3, False),
            (3, 4, True),
            (9, 10, True),
            (True, 2, False),
            (1 << 63, (1 << 63) + 1, True),
            ((1 << 63) - 1, 1 << 63, True),
        ]:
            with self.subTest(attempts=attempts):
                event: dict[str, Any] = dict(original)
                if attempts is not None:
                    event["failed_tries"] = attempts
                failed = Mock()
                with patch.object(queue, "queue_json_publish_rollback_unsafe") as publish:
                    queue.retry_event("synthetic_queue", event, failed)
                self.assertEqual(list(event), ["type", "data", "failed_tries"])
                self.assertIs(event["data"], original["data"])
                self.assertEqual(event["failed_tries"], expected)
                self.assertIs(type(event["failed_tries"]), int)
                if terminal:
                    publish.assert_not_called()
                    failed.assert_called_once()
                    self.assertIs(failed.call_args.args[0], event)
                else:
                    failed.assert_not_called()
                    publish.assert_called_once()
                    self.assertEqual(publish.call_args.args[0], "synthetic_queue")
                    self.assertIs(publish.call_args.args[1], event)

    def test_native_plan_does_not_mutate_or_accept_custom_values(self) -> None:
        backend = queue._queue_retry_backend
        if backend is None or not hasattr(backend, "plan_prepared_queue_retry"):
            self.skipTest("No prepared queue retry ABI")
        event = {"failed_tries": 3}
        self.assertEqual(backend.plan_prepared_queue_retry(event, 3), (4, True))
        self.assertEqual(event, {"failed_tries": 3})
        for invalid in (True, "1", None, 1 << 80, (1 << 63) - 1):
            self.assertIsNone(backend.plan_prepared_queue_retry({"failed_tries": invalid}, 3))
        with (
            patch.object(
                backend, "plan_prepared_queue_retry", wraps=backend.plan_prepared_queue_retry
            ) as native,
            patch.object(queue, "queue_json_publish_rollback_unsafe"),
        ):
            queue.retry_event("synthetic_queue", {}, Mock())
        native.assert_called_once()
