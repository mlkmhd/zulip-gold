from datetime import timedelta
from importlib import import_module
from random import Random
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson
import time_machine
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now as timezone_now

from zerver.actions.users import change_user_is_active
from zerver.lib import user_status
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserProfile, UserStatus
from zerver.models.clients import get_client
from zerver.tornado.django_api import EventQueueData

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse

Row = tuple[int, bool, str, str, str, str]


def extension() -> ModuleType:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(backend, "format_visible_user_statuses", None)):
        raise SkipTest("Rust extension does not support status batches")
    return backend


class UserStatusBatchRustTest(ZulipTestCase):
    def test_seeded_batches_and_exact_decline(self) -> None:
        backend = extension()
        rng = Random(815624)
        for _case in range(600):
            rows: list[Row] = [
                (
                    rng.randrange(64),
                    bool(rng.randrange(2)),
                    rng.choice(("", "at lunch", "سلام", "📝")),
                    rng.choice(("", "car", "realm test")),
                    rng.choice(("", "1f697", "1")),
                    rng.choice((UserStatus.UNICODE_EMOJI, "realm_emoji")),
                )
                for _row in range(rng.randrange(90))
            ]
            with patch.object(user_status, "_rust_user_status", None):
                expected = {
                    str(user_id): user_status.format_user_status(
                        {
                            "user_profile_id": user_id,
                            "user_profile__presence_enabled": enabled,
                            "status_text": text,
                            "emoji_name": name,
                            "emoji_code": code,
                            "reaction_type": kind,
                        }
                    )
                    for user_id, enabled, text, name, code, kind in rows
                }
            actual = backend.format_visible_user_statuses(rows)
            self.assertEqual(orjson.dumps(actual), orjson.dumps(expected))

        class CustomStatus(str):
            __slots__ = ()

        for unsupported in (
            [(2**80, True, "", "", "", "")],
            [(1, 1, "", "", "", "")],
            [(1, True, CustomStatus("ordinary text"), "", "", "")],
            [(1, True, "", "")],
        ):
            self.assertIsNone(backend.format_visible_user_statuses(unsupported))

    def test_registration_preserves_django_visibility_bytes_and_rollback(self) -> None:
        backend = extension()
        self.set_up_db_for_testing_user_access()
        visible = self.example_user("hamlet")
        hidden = self.example_user("cordelia")
        guest = self.example_user("polonius")
        inactive = self.example_user("othello")
        other_realm = self.mit_user("sipbtest")
        client = get_client("website")
        visible.presence_enabled = False
        visible.save(update_fields=["presence_enabled"])
        change_user_is_active(inactive, False)
        for profile, text in (
            (visible, "Visible آزمایش"),
            (hidden, "Restricted example"),
            (guest, "Own example"),
            (inactive, "Inactive example"),
            (other_realm, "Different organization"),
        ):
            UserStatus.objects.create(
                user_profile=profile,
                client=client,
                timestamp=timezone_now(),
                status_text=text,
                emoji_name="car" if profile == visible else "",
                emoji_code="1f697" if profile == visible else "",
                reaction_type=UserStatus.UNICODE_EMOJI,
            )

        witness = Mock(wraps=backend.format_visible_user_statuses)
        decline = Mock(return_value=None)
        native = SimpleNamespace(
            format_visible_user_statuses=witness, format_user_status=backend.format_user_status
        )
        old = SimpleNamespace(format_user_status=backend.format_user_status)
        fallback = SimpleNamespace(
            format_visible_user_statuses=decline, format_user_status=backend.format_user_status
        )
        params = {"event_types": orjson.dumps(["user_status"]).decode()}
        queue = EventQueueData(queue_id="private-status-batch-queue", idle_queue_timeout_secs=600)

        def register(actor: UserProfile, session: bool) -> "TestHttpResponse":
            if session:
                return self.client_post("/json/register", params)
            return self.api_post(actor, "/api/v1/register", params)

        t = timezone_now() + timedelta(seconds=1)
        with (
            time_machine.travel(t, tick=False),
            patch("zerver.lib.events.request_event_queue", return_value=queue),
            patch("zerver.lib.events.get_user_events", return_value=[]),
        ):
            for actor in (visible, guest):
                for session in (False, True):
                    self.logout()
                    if session:
                        self.login_user(actor)
                    with patch.object(user_status, "_rust_user_status", None):
                        expected = register(actor, session)
                    data = self.assert_json_success(expected)["user_status"]
                    self.assertEqual(data[str(visible.id)]["status_text"], "Visible آزمایش")
                    self.assertTrue(data[str(visible.id)]["away"])
                    self.assertEqual(data[str(visible.id)]["emoji_code"], "1f697")
                    self.assertNotIn(str(inactive.id), data)
                    self.assertNotIn(str(other_realm.id), data)
                    self.assertEqual(str(hidden.id) in data, actor == visible)
                    for candidate in (native, old, fallback):
                        with patch.object(user_status, "_rust_user_status", candidate):
                            actual = register(actor, session)
                        self.assertEqual(actual.status_code, expected.status_code)
                        self.assertEqual(actual.content, expected.content)
                    rows = witness.call_args.args[0]
                    self.assertEqual({str(row[0]) for row in rows}, data.keys())
                    self.assertTrue(all(len(row) == 6 for row in rows))
        self.assertEqual(witness.call_count, 4)
        self.assertEqual(decline.call_count, 4)

        # Each implementation, including a native decline, uses only one
        # query for the visible rows of this unrestricted synthetic actor.
        for backend_mode in (None, native, old, fallback):
            with (
                patch.object(user_status, "_rust_user_status", backend_mode),
                CaptureQueriesContext(connection) as queries,
            ):
                matrix = user_status.get_all_users_status_dict(visible.realm, visible)
            self.assertIn(str(visible.id), matrix)
            self.assert_length(queries.captured_queries, 1)
