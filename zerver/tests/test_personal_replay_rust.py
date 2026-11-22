from collections.abc import Callable, Iterable
from importlib import import_module
from random import Random
from types import SimpleNamespace
from typing import Any
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson

from zerver.lib import native_personal_replay
from zerver.lib.events import apply_events
from zerver.lib.test_classes import ZulipTestCase


def extension() -> Callable[[object, object], bool]:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    apply = getattr(backend, "apply_personal_list_event", None)
    if not callable(apply):
        raise SkipTest("Rust extension does not support personal-list replay")
    return apply


class PersonalReplayRustTest(ZulipTestCase):
    def replay(
        self,
        state: dict[str, Any],
        events: Iterable[dict[str, Any]],
        *,
        fetch: set[str] | None = None,
    ) -> None:
        apply_events(
            self.example_user("hamlet"),
            state=state,
            events=events,
            fetch_event_types=fetch,
            client_gravatar=False,
            slim_presence=True,
            include_subscribers=False,
            linkifier_url_template=False,
            user_list_incomplete=False,
            include_deactivated_groups=False,
        )

    def test_first_match_and_owner_list_identities(self) -> None:
        native = extension()
        snippet = {"id": 1, "title": "Existing"}
        duplicate = {"id": 1, "title": "Duplicate"}
        edited = {"id": 1, "title": "Edited"}
        saved = [snippet, duplicate]
        draft = {"id": 2, "content": "Existing"}
        first = {"id": 3, "content": "First new draft"}
        second = {"id": 3, "content": "Second new draft"}
        drafts = [draft]
        view = {"fragment": "inbox", "name": None, "is_pinned": True}
        views = [view]
        state = {"saved_snippets": saved, "drafts": drafts, "navigation_views": views}
        calls: list[bool] = []

        def witness(selected: object, event: object) -> bool:
            self.assertIsInstance(selected, list)
            claimed = native(selected, event)
            calls.append(claimed)
            return claimed

        with patch.object(
            native_personal_replay, "_backend", SimpleNamespace(apply_personal_list_event=witness)
        ):
            self.replay(
                state,
                [
                    {"type": "saved_snippets", "op": "update", "saved_snippet": edited},
                    {"type": "drafts", "op": "add", "drafts": [draft, first, second]},
                    {
                        "type": "navigation_view",
                        "op": "update",
                        "fragment": "inbox",
                        "data": {"name": "Inbox", "is_pinned": False},
                    },
                ],
            )
            self.assertEqual(saved, [edited, duplicate])
            self.assertIs(saved[0], edited)
            self.assertIs(saved[1], duplicate)
            self.assertEqual(drafts, [draft, first, second])
            self.assertIs(drafts[1], first)
            self.assertIs(drafts[2], second)
            self.assertIs(views[0], view)
            self.assertEqual(view, {"fragment": "inbox", "name": "Inbox", "is_pinned": False})
            self.replay(
                state,
                [{"type": "saved_snippets", "op": "remove", "saved_snippet_id": 1}],
            )
            self.assertEqual(saved, [duplicate])
            self.replay(
                state,
                [{"type": "navigation_view", "op": "remove", "fragment": "inbox"}],
                fetch={"drafts"},
            )
        self.assertEqual(calls, [True] * 4)
        self.assertIs(state["saved_snippets"], saved)
        self.assertIs(state["drafts"], drafts)
        self.assertIs(state["navigation_views"], views)

    def test_differential_on_real_reducer_with_legacy_fallback(self) -> None:
        native = extension()
        generator = Random(7720)
        events: list[dict[str, Any]] = []
        for kind, card_key, remove_key in (
            ("saved_snippets", "saved_snippet", "saved_snippet_id"),
            ("drafts", "draft", "draft_id"),
            ("navigation_view", "navigation_view", "fragment"),
        ):
            for n in range(180):
                op = generator.choice(("add", "update", "remove"))
                identifier = generator.randrange(12)
                payload: dict[str, Any]
                if kind == "navigation_view":
                    fragment = f"channel/{identifier}/موضوع"
                    if op == "add":
                        payload = {card_key: {"fragment": fragment, "name": str(n)}}
                    elif op == "update":
                        payload = {"fragment": fragment, "data": {"name": str(n), "pin": True}}
                    else:
                        payload = {"fragment": fragment}
                elif op == "remove":
                    payload = {remove_key: identifier}
                elif op == "add" and kind == "drafts":
                    payload = {"drafts": [{"id": identifier, "content": str(n)}]}
                else:
                    payload = {card_key: {"id": identifier, "content": str(n)}}
                events.append({"type": kind, "op": op, **payload})

        def timeline(backend: object) -> list[bytes]:
            state: dict[str, Any] = {"saved_snippets": [], "drafts": [], "navigation_views": []}
            history = []
            with patch.object(native_personal_replay, "_backend", backend):
                for event in events:
                    # The navigation reducer mutates the original added card.
                    # Give each replay the same fresh JSON payloads.
                    self.replay(state, [orjson.loads(orjson.dumps(event))])
                    history.append(orjson.dumps(state))
            return history

        reference = timeline(None)
        accepted: list[bool] = []

        def witness(selected: object, event: object) -> bool:
            claimed = native(selected, event)
            accepted.append(claimed)
            return claimed

        self.assertEqual(timeline(SimpleNamespace(apply_personal_list_event=witness)), reference)
        self.assertEqual(accepted, [True] * len(events))
        self.assertEqual(timeline(SimpleNamespace()), reference)
        decline = Mock(return_value=False)
        self.assertEqual(timeline(SimpleNamespace(apply_personal_list_event=decline)), reference)
        self.assertEqual(decline.call_count, len(events))

    def test_native_declines_before_mutation_for_legacy_mapping_and_identifier(self) -> None:
        native = extension()
        row = {"id": 6, "content": "Existing"}
        drafts = [row]
        state = {"drafts": drafts}
        first = {"id": 7, "content": "First"}
        second = {"id": 2**100, "content": "Large identifier"}
        event = {"type": "drafts", "op": "add", "drafts": [first, second]}
        self.assertFalse(native(drafts, event))
        self.assertEqual(drafts, [row])
        view = {"fragment": "inbox", "name": None}
        legacy = {
            "type": "navigation_view",
            "op": "update",
            "fragment": "inbox",
            "data": [("name", "Inbox")],
        }
        self.assertFalse(native([view], legacy))
        self.assertIsNone(view["name"])
        with patch.object(
            native_personal_replay, "_backend", SimpleNamespace(apply_personal_list_event=native)
        ):
            self.replay(state, [event])
            self.replay({"navigation_views": [view]}, [legacy])
        self.assertEqual(drafts, [row, first, second])
        self.assertIs(drafts[1], first)
        self.assertIs(drafts[2], second)
        self.assertEqual(view, {"fragment": "inbox", "name": "Inbox"})
