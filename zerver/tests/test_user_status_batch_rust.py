from datetime import timedelta
from importlib import import_module
from os import environ
from random import Random
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson
import time_machine
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now as timezone_now

from zerver.actions.users import change_user_is_active
from zerver.lib import native_user_status_replay, user_status
from zerver.lib.native_backend import supports_default_rust_backend
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import stub_event_queue_user_events
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
    def test_matching_wheel_formats_reads_without_a_flag(self) -> None:
        if not supports_default_rust_backend() or environ.get("ZULIP_RUST_USER_STATUS"):
            self.skipTest("No-flag startup is tested on Ubuntu 24.04 x86-64")
        backend = extension()
        if getattr(backend, "USER_STATUS_READ_API", None) != 1:
            self.skipTest("Wheel predates the default status formatter contract")
        self.assertIs(user_status._rust_user_status, backend)
        self.assertEqual(user_status._prepare_status_update.__module__, user_status.__name__)
        owner = self.example_user("hamlet")
        UserStatus.objects.create(
            user_profile=owner,
            client=get_client("website"),
            timestamp=timezone_now(),
            status_text="وضعیت آزمایشی",
            emoji_name="car",
            emoji_code="1f697",
            reaction_type=UserStatus.UNICODE_EMOJI,
        )

        queue = EventQueueData(queue_id="native-status-read-queue", idle_queue_timeout_secs=600)
        with (
            patch.object(backend, "format_user_status", wraps=backend.format_user_status) as single,
            patch.object(
                backend, "format_visible_user_statuses", wraps=backend.format_visible_user_statuses
            ) as batch,
            patch("zerver.lib.events.request_event_queue", return_value=queue),
            patch("zerver.lib.events.get_user_events", return_value=[]),
        ):
            status_result = self.api_get(owner, f"/api/v1/users/{owner.id}/status")
            register_result = self.api_post(
                owner, "/api/v1/register", {"event_types": orjson.dumps(["user_status"]).decode()}
            )

        status = self.assert_json_success(status_result)["status"]
        self.assertEqual(status["status_text"], "وضعیت آزمایشی")
        self.assertEqual(status["emoji_code"], "1f697")
        self.assertEqual(
            self.assert_json_success(register_result)["user_status"][str(owner.id)], status
        )
        single.assert_called_once()
        batch.assert_called_once()

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

    def test_existing_status_replay_preserves_order_and_declines_atomically(self) -> None:
        backend = extension()
        native = getattr(backend, "apply_user_status_row_event", None)
        if not callable(native):
            self.skipTest("Rust extension does not support status replay")

        row = {"away": True, "status_text": "سلام", "emoji_name": "car", "emoji_code": "1f697"}
        alias = row
        ignored = {"emoji_name": "", "emoji_code": "", "reaction_type": "unicode_emoji"}
        self.assertTrue(native(row, ignored))
        self.assertEqual(list(row), ["away", "status_text", "emoji_name", "emoji_code"])

        before = list(row.items())
        bad = {**ignored, "away": False, "status_text": "", "emoji_code": 17}
        self.assertIsNone(native(row, bad))
        self.assertIsNone(native(row, row))
        self.assertEqual(list(row.items()), before)
        noncanonical = {"away": True, "status_text": 42}
        self.assertIsNone(native(noncanonical, {"away": False}))
        self.assertEqual(noncanonical, {"away": True, "status_text": 42})

        self.assertTrue(native(row, {**ignored, "status_text": "🙂"}))
        self.assertIs(row, alias)
        self.assertEqual(list(row.items()), [("away", True), ("status_text", "🙂")])
        self.assertIs(native(row, {"away": False, "status_text": ""}), False)
        self.assertEqual(alias, {})

    def test_register_status_events_preserve_both_routes_and_fallback(self) -> None:
        backend = extension()
        native = getattr(backend, "apply_user_status_row_event", None)
        if not callable(native):
            self.skipTest("Rust extension does not support status replay")
        enabled = environ.get("ZULIP_RUST_USER_STATUS_REPLAY") == "1"

        owner = self.example_user("hamlet")
        peer = self.example_user("cordelia")
        other = self.example_user("othello")
        UserStatus.objects.create(
            user_profile=owner,
            client=get_client("website"),
            timestamp=timezone_now(),
            status_text="Before",
            emoji_name="car",
            emoji_code="1f697",
            reaction_type=UserStatus.UNICODE_EMOJI,
        )

        def change(number: int, user: UserProfile, **fields: object) -> dict[str, Any]:
            return {"id": number, "type": "user_status", "user_id": user.id, **fields}

        events = [
            change(1, peer, away=True),
            change(
                2,
                owner,
                away=True,
                status_text="سلام",
                emoji_name="",
                emoji_code="",
                reaction_type=UserStatus.UNICODE_EMOJI,
            ),
            change(3, peer, away=False),
            change(
                4,
                other,
                status_text="🙂 فارسی",
                emoji_name="car",
                emoji_code="1f697",
                reaction_type=UserStatus.UNICODE_EMOJI,
            ),
            change(5, other, emoji_name=""),
            change(6, owner, away=False, status_text=""),
            change(7, owner, away=True),
        ]
        params = {"event_types": orjson.dumps(["user_status"]).decode()}
        queue = EventQueueData(queue_id="15:21", idle_queue_timeout_secs=600)
        self.login_user(owner)

        def request(session: bool) -> tuple[int, bytes]:
            with stub_event_queue_user_events(queue, events):
                response = (
                    self.client_post("/json/register", params)
                    if session
                    else self.api_post(owner, "/api/v1/register", params)
                )
            self.assert_json_success(response)
            return response.status_code, response.content

        def decline_one(row: dict[str, Any], event: dict[str, Any]) -> bool | None:
            if event["id"] == 2:
                before = list(row.items())
                self.assertIsNone(native(row, {**event, "emoji_code": 17}))
                self.assertEqual(list(row.items()), before)
                return None
            return native(row, event)

        native_results: list[bool | None] = []

        def witness(row: dict[str, Any], event: dict[str, Any]) -> bool | None:
            result: bool | None = native(row, event)
            native_results.append(result)
            return result

        with time_machine.travel(timezone_now() + timedelta(seconds=1), tick=False):
            for session in (False, True):
                with self.subTest(session=session):
                    with patch.object(native_user_status_replay, "_backend", None):
                        expected = request(session)
                    data = orjson.loads(expected[1])
                    self.assertEqual(data["last_event_id"], 7)
                    self.assertEqual(list(data["user_status"]), [str(other.id), str(owner.id)])
                    self.assertEqual(
                        list(data["user_status"][str(other.id)].items()),
                        [
                            ("status_text", "🙂 فارسی"),
                            ("emoji_name", "car"),
                            ("emoji_code", "1f697"),
                            ("reaction_type", UserStatus.UNICODE_EMOJI),
                        ],
                    )
                    self.assertEqual(data["user_status"][str(owner.id)], {"away": True})

                    native_results.clear()
                    with patch.object(backend, "apply_user_status_row_event", side_effect=witness):
                        self.assertEqual(request(session), expected)
                    self.assertEqual(
                        native_results,
                        [True, True, False, True, True, False, True] if enabled else [],
                    )

                    decline = Mock(side_effect=decline_one)
                    for candidate in (
                        SimpleNamespace(
                            format_visible_user_statuses=backend.format_visible_user_statuses
                        ),
                        SimpleNamespace(apply_user_status_row_event=decline),
                    ):
                        with patch.object(native_user_status_replay, "_backend", candidate):
                            self.assertEqual(request(session), expected)
                    self.assertEqual(decline.call_count, len(events))

    def test_adapter_declines_foreign_rows_and_container_aliases(self) -> None:
        backend = extension()
        if not callable(getattr(backend, "apply_user_status_row_event", None)):
            self.skipTest("Rust extension does not support status replay")
        event = {"away": False}
        wrong_row = {"away": True, "status_text": 42}
        statuses = {"7": wrong_row}
        with patch.object(native_user_status_replay, "_backend", backend):
            self.assertFalse(native_user_status_replay.apply(statuses, "7", event))
            self.assertIs(statuses["7"], wrong_row)
            self.assertEqual(wrong_row, {"away": True, "status_text": 42})

            outer_alias: dict[str, object] = {}
            outer_alias["7"] = outer_alias
            self.assertFalse(native_user_status_replay.apply(outer_alias, "7", event))
            self.assertIs(outer_alias["7"], outer_alias)

            event_alias = {"7": event}
            self.assertFalse(native_user_status_replay.apply(event_alias, "7", event))
            self.assertIs(event_alias["7"], event)
            self.assertEqual(event, {"away": False})

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
