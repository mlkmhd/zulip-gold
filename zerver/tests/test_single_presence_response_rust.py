from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import time_machine
from django.test import override_settings

from zerver.lib import native_presence
from zerver.lib.test_classes import ZulipTestCase


class SinglePresenceResponseContractTest(ZulipTestCase):
    def test_offline_boundary_keeps_the_website_and_modern_fields(self) -> None:
        user = self.example_user("hamlet")
        self.login_user(user)
        active = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
        with time_machine.travel(active, tick=False):
            self.assert_json_success(
                self.client_post("/json/users/me/presence", {"status": "active"})
            )

        responses = []
        for seconds in (10, 11):
            with (
                override_settings(OFFLINE_THRESHOLD_SECS=10),
                time_machine.travel(active + timedelta(seconds=seconds), tick=False),
            ):
                response = self.assert_json_success(
                    self.client_get(f"/json/users/{user.id}/presence")
                )
            responses.append(response["presence"])

        moment = int(active.timestamp())
        for result, status in zip(responses, ("active", "offline"), strict=True):
            self.assertEqual(
                result,
                {
                    "active_timestamp": moment,
                    "idle_timestamp": moment,
                    "aggregated": {"status": status, "timestamp": moment},
                    "website": {"status": "active", "timestamp": moment},
                },
            )

    def test_exact_finalizer_does_not_enable_batched_selection(self) -> None:
        backend = native_presence._single_response_backend
        if getattr(backend, "PRESENCE_SINGLE_USER_RESPONSE_API", None) != 1:
            self.skipTest("Matching Rust single presence response contract is not available")
        payload = {
            "aggregated": {"status": "active", "timestamp": 17, "client": "website"},
            "website": {"status": "active", "timestamp": 17, "pushable": False},
        }
        with (
            patch.object(native_presence, "_backend", None),
            patch.object(
                backend,
                "finish_single_user_presence",
                wraps=backend.finish_single_user_presence,
            ) as finalize,
        ):
            self.assertFalse(native_presence.is_enabled())
            self.assertIsNone(native_presence.format_rows([], None))
            result = native_presence.finish_single_user({"active_timestamp": 17}, payload, 11, 10)
        finalize.assert_called_once()
        assert result is not None
        self.assertEqual(result["aggregated"], {"status": "offline", "timestamp": 17})
        self.assertEqual(result["website"], {"status": "active", "timestamp": 17})
