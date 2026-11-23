from importlib import import_module
from random import Random
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING
from unittest import SkipTest
from unittest.mock import Mock, patch

from django.test import override_settings
from django.utils.timezone import now as timezone_now

from zerver.actions.realm_settings import do_set_realm_property
from zerver.lib import native_topic_history
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.topic import generate_topic_history_from_db_rows
from zerver.models import Message, Stream, UserMessage
from zerver.models.clients import get_client

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


def extension() -> ModuleType:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(backend, "format_visible_topic_history", None)):
        raise SkipTest("Rust extension does not support visible topic history")
    return backend


class TopicHistoryRustTest(ZulipTestCase):
    def test_ordinary_rows_case_order_and_legacy_shape(self) -> None:
        backend = extension()
        rng = Random(780319)
        spellings = ("", "general chat", "Topic", "tOPIC", "İ", "i\u0307", "AΣ", "aς", "✔ آزمایش")
        witness = Mock(wraps=backend.format_visible_topic_history)
        native = SimpleNamespace(format_visible_topic_history=witness)
        for _case in range(600):
            rows = [(rng.choice(spellings), value) for value in range(rng.randrange(70), 0, -1)]
            for allow_empty in (True, False):
                with patch.object(native_topic_history, "_backend", None):
                    reference = generate_topic_history_from_db_rows(rows, allow_empty)
                with patch.object(native_topic_history, "_backend", native):
                    result = generate_topic_history_from_db_rows(rows, allow_empty)
                self.assertEqual(result, reference)
                self.assertTrue(all(list(entry) == ["name", "max_id"] for entry in result))
        self.assertEqual(witness.call_count, 1200)
        self.assertEqual(
            backend.format_visible_topic_history(
                [("", 2), ("general chat", 1)], False, "legacy label"
            ),
            [{"name": "legacy label", "max_id": 2}, {"name": "general chat", "max_id": 1}],
        )

    def test_decline_unsorted_nonordinary_and_old_extensions(self) -> None:
        backend = extension()

        class CustomTopic(str):
            __slots__ = ()

        cases: tuple[list[tuple[str, int]], ...] = (
            [("Case", 2), ("other", 3)],
            [("Same", 1), ("sAME", 1)],
            [("Huge", 2**80)],
            [(CustomTopic("exact only"), 4)],
        )
        for rows in cases:
            self.assertIsNone(backend.format_visible_topic_history(rows, True, "legacy"))
            with patch.object(native_topic_history, "_backend", None):
                reference = generate_topic_history_from_db_rows(rows, True)
            for candidate in (backend, SimpleNamespace()):
                with patch.object(native_topic_history, "_backend", candidate):
                    candidate_result = generate_topic_history_from_db_rows(rows, True)
                self.assertEqual(candidate_result, reference)

    def test_authenticated_and_anonymous_visible_rows_and_rollback(self) -> None:
        backend = extension()
        reader = self.example_user("hamlet")
        writer = self.example_user("cordelia")
        self.assertTrue(writer.realm.web_public_streams_enabled())
        public = self.make_stream("Topic projection public", is_web_public=True)
        protected = self.make_stream(
            "Topic projection protected", invite_only=True, history_public_to_subscribers=False
        )
        self.subscribe(reader, protected.name)
        self.subscribe(writer, protected.name)
        client = get_client("website")

        def ordinary_message(channel: Stream, topic: str) -> int:
            assert channel.recipient_id is not None
            message = Message(
                realm_id=writer.realm_id,
                recipient_id=channel.recipient_id,
                sender=writer,
                sending_client=client,
                date_sent=timezone_now(),
                content="Synthetic topic history.",
            )
            message.set_topic_name(topic)
            message.save(force_insert=True)
            return message.id

        ordinary_message(public, "Aς")
        chosen = ordinary_message(public, "AΣ")
        literal = ordinary_message(public, "general chat")
        empty = ordinary_message(public, "")
        hidden = ordinary_message(protected, "Earlier protected topic")
        visible = ordinary_message(protected, "Visible protected topic")
        UserMessage.objects.create(user_profile=reader, message_id=visible)
        witness = Mock(wraps=backend.format_visible_topic_history)
        decline = Mock(return_value=None)
        native = SimpleNamespace(format_visible_topic_history=witness)

        def fetch(route: str, channel_id: int, allow_empty: bool) -> "TestHttpResponse":
            params = {"allow_empty_topic_name": "true" if allow_empty else "false"}
            if route == "basic":
                return self.api_get(reader, f"/api/v1/users/me/{channel_id}/topics", params)
            return self.client_get(f"/json/users/me/{channel_id}/topics", params)

        def compare_request(
            route: str, channel: Stream, allow_empty: bool, exact: list[tuple[str, int]]
        ) -> None:
            with patch.object(native_topic_history, "_backend", None):
                reference = fetch(route, channel.id, allow_empty)
            payload = self.assert_json_success(reference)
            self.assertEqual(payload["topics"], [{"name": n, "max_id": i} for n, i in exact])
            before = witness.call_count
            for candidate in (
                native,
                SimpleNamespace(),
                SimpleNamespace(format_visible_topic_history=decline),
            ):
                with patch.object(native_topic_history, "_backend", candidate):
                    response = fetch(route, channel.id, allow_empty)
                self.assertEqual(response.status_code, reference.status_code)
                self.assertEqual(response.content, reference.content)
            self.assertEqual(witness.call_count, before + 1)
            projected = witness.call_args.args[0]
            self.assertNotIn(hidden, [max_id for _name, max_id in projected])
            if channel.id == protected.id:
                self.assertEqual(projected, [("Visible protected topic", visible)])

        for route in ("basic", "session", "spectator"):
            self.logout()
            if route == "session":
                self.login_user(reader)
            for allow_empty in (False, True):
                with self.subTest(route=route, allow_empty=allow_empty):
                    compare_request(
                        route,
                        public,
                        allow_empty,
                        [
                            ("" if allow_empty else "general chat", empty),
                            ("general chat", literal),
                            ("AΣ", chosen),
                        ],
                    )
                    if route != "spectator":
                        compare_request(
                            route, protected, allow_empty, [("Visible protected topic", visible)]
                        )

        self.assertEqual(witness.call_count, 10)
        self.assertEqual(decline.call_count, 10)
        self.logout()
        for candidate in (None, native):
            with patch.object(native_topic_history, "_backend", candidate):
                with override_settings(WEB_PUBLIC_STREAMS_ENABLED=False):
                    self.assert_json_error(
                        fetch("spectator", public.id, True),
                        "Not logged in: API authentication or user session required",
                        status_code=401,
                    )
                self.assert_json_error(fetch("spectator", protected.id, True), "Invalid channel ID")
        do_set_realm_property(writer.realm, "enable_spectator_access", False, acting_user=None)
        with patch.object(native_topic_history, "_backend", native):
            self.assert_json_error(
                fetch("spectator", public.id, True),
                "Not logged in: API authentication or user session required",
                status_code=401,
            )
        self.assertEqual(witness.call_count, 10)
