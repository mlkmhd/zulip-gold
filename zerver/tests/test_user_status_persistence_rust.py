from datetime import timedelta, timezone
from importlib import import_module
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING
from unittest import SkipTest
from unittest.mock import Mock, patch

import time_machine
from django.db import connection
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now as timezone_now

from zerver.actions.user_status import do_update_user_status
from zerver.lib import native_user_status_persistence
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.user_status import update_user_status
from zerver.models import UserStatus
from zerver.models.clients import get_client

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


def extension() -> ModuleType:
    try:
        module = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(module, "update_existing_user_status", None)):
        raise SkipTest("Rust extension does not support status persistence")
    return module


class UserStatusPersistenceRustTest(ZulipTestCase):
    def setUp(self) -> None:
        super().setUp()
        disable_prepared = patch.object(native_user_status_persistence, "_prepared_backend", None)
        disable_prepared.start()
        self.addCleanup(disable_prepared.stop)

    def test_creation_fallback_and_existing_update_query_count(self) -> None:
        backend = extension()
        owner = self.example_user("hamlet")
        first_client = get_client("website")
        second_client = get_client("apitest")
        witness = Mock(wraps=backend.update_existing_user_status)
        native = SimpleNamespace(update_existing_user_status=witness)
        clock = timezone_now()
        with (
            patch.object(native_user_status_persistence, "_backend", native),
            time_machine.travel(clock, tick=False),
        ):
            update_user_status(owner.id, "Before", first_client.id, "car", "1f697", "unicode_emoji")
        row = UserStatus.objects.get(user_profile=owner)
        self.assertEqual(
            (row.status_text, row.client_id, row.timestamp), ("Before", first_client.id, clock)
        )
        self.assertEqual(witness.call_count, 1)

        def update_and_measure(candidate: SimpleNamespace | None) -> int:
            with (
                patch.object(native_user_status_persistence, "_backend", candidate),
                CaptureQueriesContext(connection) as queries,
            ):
                # No supplied emoji name means existing emoji fields stay
                # untouched, even if a lower-level caller supplied a code.
                update_user_status(owner.id, "", second_client.id, None, "ignored", "realm_emoji")
            row.refresh_from_db()
            self.assertEqual((row.status_text, row.client_id), ("", second_client.id))
            self.assertEqual(
                (row.emoji_name, row.emoji_code, row.reaction_type),
                ("car", "1f697", "unicode_emoji"),
            )
            return sum("zerver_userstatus" in query["sql"] for query in queries)

        self.assertEqual(update_and_measure(None), 2)
        self.assertEqual(update_and_measure(native), 1)
        self.assertEqual(witness.call_count, 2)
        self.assertEqual(witness.call_args.args[-1], ("", None, None, None))
        self.assertEqual(update_and_measure(SimpleNamespace()), 2)
        decline = Mock(return_value=False)
        self.assertEqual(
            update_and_measure(SimpleNamespace(update_existing_user_status=decline)), 2
        )
        decline.assert_called_once()
        self.assertEqual(UserStatus.objects.filter(user_profile=owner).count(), 1)

    def test_api_session_events_and_django_visibility(self) -> None:
        backend = extension()
        self.set_up_db_for_testing_user_access()
        owner = self.example_user("cordelia")
        guest = self.example_user("polonius")
        client = get_client("website")
        seed = {
            "status_text": "Before",
            "emoji_name": "car",
            "emoji_code": "1f697",
            "reaction_type": UserStatus.UNICODE_EMOJI,
        }
        row = UserStatus.objects.create(
            user_profile=owner, client=client, timestamp=timezone_now(), **seed
        )
        witness = Mock(wraps=backend.update_existing_user_status)
        decline = Mock(return_value=False)
        native = SimpleNamespace(update_existing_user_status=witness)
        old = SimpleNamespace()
        fallback = SimpleNamespace(update_existing_user_status=decline)

        def post(session: bool) -> "TestHttpResponse":
            data = {"status_text": "  New status آزمایش  ", "emoji_name": ""}
            if session:
                return self.client_post("/json/users/me/status", data)
            return self.api_post(owner, "/api/v1/users/me/status", data)

        self.login_user(owner)
        with time_machine.travel(timezone_now(), tick=False):
            for session in (False, True):

                def run(
                    candidate: SimpleNamespace | None,
                    active_session: bool,
                ) -> tuple[bytes, bytes, list[dict[str, object]]]:
                    UserStatus.objects.filter(id=row.id).update(**seed)
                    with (
                        patch.object(native_user_status_persistence, "_backend", candidate),
                        self.capture_send_event_calls(expected_num_events=1) as notifications,
                    ):
                        response = post(active_session)
                    self.assert_json_success(response)
                    read = self.api_get(owner, f"/api/v1/users/{owner.id}/status")
                    self.assertEqual(
                        self.assert_json_success(read)["status"],
                        {"status_text": "New status آزمایش"},
                    )
                    row.refresh_from_db()
                    self.assertEqual(
                        (row.emoji_name, row.emoji_code, row.reaction_type),
                        ("", "", UserStatus.UNICODE_EMOJI),
                    )
                    self.assertIn(owner.id, notifications[0]["users"])
                    self.assertNotIn(guest.id, notifications[0]["users"])
                    return response.content, read.content, [dict(event) for event in notifications]

                expected = run(None, session)
                for mode in (native, old, fallback):
                    self.assertEqual(run(mode, session), expected)
        self.assertEqual(witness.call_count, 2)
        self.assertEqual(decline.call_count, 2)

    def test_orm_hooks_and_action_rollback(self) -> None:
        backend = extension()
        owner = self.example_user("hamlet")
        client = get_client("website")
        row = UserStatus.objects.create(
            user_profile=owner, client=client, timestamp=timezone_now(), status_text="Before"
        )
        witness = Mock(wraps=backend.update_existing_user_status)
        native = SimpleNamespace(update_existing_user_status=witness)
        signal = Mock()
        post_save.connect(signal, sender=UserStatus, weak=False)
        try:
            with patch.object(native_user_status_persistence, "_backend", native):
                update_user_status(owner.id, "ORM", client.id, None, None, None)
            witness.assert_not_called()
            signal.assert_called_once()
        finally:
            post_save.disconnect(signal, sender=UserStatus)

        with (
            patch.object(native_user_status_persistence, "_backend", native),
            patch(
                "zerver.actions.user_status.get_user_ids_who_can_access_user",
                side_effect=RuntimeError("synthetic abort after persistence"),
            ),
            self.capture_send_event_calls(expected_num_events=0),
            self.assertRaisesRegex(RuntimeError, "synthetic abort after persistence"),
        ):
            do_update_user_status(owner, None, "Not committed", client.id, None, None, None)
        witness.assert_called_once()
        row.refresh_from_db()
        self.assertEqual(row.status_text, "ORM")


class PreparedUserStatusPersistenceRustTest(ZulipTestCase):
    def test_default_dispatch_matches_orm_search_path(self) -> None:
        backend = native_user_status_persistence._prepared_backend
        if backend is None:
            raise SkipTest("Rust extension lacks the prepared status contract")
        owner = self.example_user("hamlet")
        client = get_client("website")
        seed = UserStatus.objects.create(
            user_profile=owner, client=client, timestamp=timezone_now(), status_text="Original"
        )
        witness = Mock(wraps=backend.update_prepared_user_status)
        with connection.cursor() as cursor:
            cursor.execute(
                'CREATE TEMP TABLE "zerver_userstatus" ON COMMIT DROP '
                'AS SELECT * FROM "zerver_userstatus" WHERE id = %s',
                [seed.id],
            )
            try:
                with patch.object(backend, "update_prepared_user_status", witness):
                    update_user_status(owner.id, "آزمایش", client.id, None, None, None)
                witness.assert_called_once()
                self.assertEqual(UserStatus.objects.get(id=seed.id).status_text, "آزمایش")
            finally:
                cursor.execute('DROP TABLE pg_temp."zerver_userstatus"')
        seed.refresh_from_db()
        self.assertEqual(seed.status_text, "Original")

    def test_decline_before_sql_for_nonstandard_inputs(self) -> None:
        backend = native_user_status_persistence._prepared_backend
        if backend is None:
            raise SkipTest("Rust extension lacks the prepared status contract")
        stamp = timezone_now()
        changes = (None, None, None, None)

        def decline() -> bool:
            return native_user_status_persistence.update_existing_status(1, 1, stamp, changes)

        with patch.object(backend, "update_prepared_user_status") as witness:
            with patch.object(UserStatus._meta, "db_table", "custom_status"):
                self.assertFalse(decline())
            with patch.object(native_user_status_persistence.router, "routers", [object()]):
                self.assertFalse(decline())
            changes = (chr(0xD800), None, None, None)
            self.assertFalse(decline())
            changes = (None, None, None, None)
            stamp = stamp.astimezone(timezone(timedelta(hours=1)))
            self.assertFalse(decline())
        witness.assert_not_called()
