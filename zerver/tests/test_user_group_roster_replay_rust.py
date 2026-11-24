from datetime import timedelta
from importlib import import_module
from os import environ
from random import Random
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson
import time_machine
from django.test import SimpleTestCase
from django.utils.timezone import now as timezone_now

from zerver.actions.user_groups import check_add_user_group
from zerver.lib import native_group_payload
from zerver.lib.events import apply_events
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import stub_event_queue_user_events
from zerver.tornado.django_api import EventQueueData


def extension() -> ModuleType:
    try:
        native = import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        raise SkipTest("Rust extension is not installed") from None
    for field in ("append_and_sort_register_group_ids", "subtract_register_group_ids"):
        if not callable(getattr(native, field, None)):
            raise SkipTest("Rust extension does not support register group roster replay")
    return native


class UserGroupRosterReplayRustTest(SimpleTestCase):
    def test_duplicate_ids_order_and_aliasing(self) -> None:
        native = extension()
        x = int("98765")
        y = int("98765")
        self.assertIsNot(x, y)
        roster = [11, x, 4]
        alias = roster

        self.assertTrue(native.append_and_sort_register_group_ids(roster, [8, y, 4]))
        self.assertIs(roster, alias)
        self.assertEqual(roster, [4, 4, 8, 11, x, y])
        self.assertIs(roster[-2], x)
        self.assertIs(roster[-1], y)

        removed = native.subtract_register_group_ids(roster, [8, 8, -22])
        self.assertEqual(removed, [4, 11, x])
        self.assertIsNot(removed, roster)
        self.assertIs(removed[-1], x)
        self.assertEqual(roster, [4, 4, 8, 11, x, y])

        self.assertTrue(native.append_and_sort_register_group_ids(roster, roster))
        self.assertEqual(roster, [4, 4, 4, 4, 8, 8, 11, 11, x, x, y, y])
        self.assertEqual(native.subtract_register_group_ids(roster, roster), [])

    def test_ordered_operations_and_atomic_decline(self) -> None:
        native = extension()
        rng = Random(92015)
        prototype = [2**63 - 1, -(2**63), 1001, 1001, 3]
        expected, actual = prototype.copy(), prototype.copy()
        for index in range(500):
            incoming = rng.choices(prototype, k=rng.randrange(20))
            if index % 3:
                expected.extend(incoming)
                expected.sort()
                self.assertTrue(native.append_and_sort_register_group_ids(actual, incoming))
            else:
                expected = sorted(set(expected) - set(incoming))
                actual = native.subtract_register_group_ids(actual, incoming)
            self.assertEqual(actual, expected)

        for malformed in ([1, True], [1, 2**100], (1, 2), [1, "2"]):
            roster = prototype.copy()
            self.assertFalse(native.append_and_sort_register_group_ids(roster, malformed))
            self.assertIsNone(native.subtract_register_group_ids(roster, malformed))
            self.assertEqual(roster, prototype)


class UserGroupRosterRegistrationRustTest(ZulipTestCase):
    def test_late_duplicate_decline_falls_back_only_on_that_card(self) -> None:
        backend = extension()
        owner = self.example_user("hamlet")

        for suffix, field, incoming in (
            ("members", "members", "user_ids"),
            ("subgroups", "direct_subgroup_ids", "direct_subgroup_ids"),
        ):
            with self.subTest(field=field):
                accepted = [2]
                declined = [True]
                untouched = [9]
                cards = [
                    {"id": 1, field: accepted},
                    {"id": 1, field: declined},
                    {"id": 9, field: untouched},
                ]
                state = {"realm_user_groups": cards}
                events = [
                    {
                        "type": "user_group",
                        "op": f"{operation}_{suffix}",
                        "group_id": 1,
                        incoming: ids,
                    }
                    for operation, ids in (("add", [3]), ("remove", [3]), ("remove", [2, 1]))
                ]
                with patch.object(native_group_payload, "_roster_replay_backend", backend):
                    for index, event in enumerate(events):
                        apply_events(
                            owner,
                            state=state,
                            events=[event],
                            fetch_event_types=None,
                            client_gravatar=False,
                            slim_presence=True,
                            include_subscribers=False,
                            linkifier_url_template=False,
                            user_list_incomplete=False,
                            include_deactivated_groups=False,
                        )
                        if index == 0:
                            self.assertIs(cards[0][field], accepted)
                            self.assertIs(cards[1][field], declined)
                            self.assertEqual(accepted, [2, 3])
                            self.assertEqual(declined, [True, 3])
                        else:
                            self.assertIsNot(cards[0][field], accepted)
                            self.assertIsNot(cards[1][field], declined)
                            self.assertEqual(cards[0][field], [2] if index == 1 else [])
                            self.assertEqual(cards[1][field], [True] if index == 1 else [])
                            self.assertEqual(accepted, [2, 3])
                            self.assertEqual(declined, [True, 3])
                        self.assertIs(cards[2][field], untouched)
                        self.assertEqual(untouched, [9])

    def test_real_basic_and_session_registration_replays_selected_ids(self) -> None:
        backend = extension()
        append = backend.append_and_sort_register_group_ids
        subtract = backend.subtract_register_group_ids
        enabled = environ.get("ZULIP_RUST_GROUP_ROSTER_REPLAY") == "1"
        owner = self.example_user("hamlet")
        peer = self.example_user("cordelia")
        other = self.example_user("othello")
        selected = check_add_user_group(
            owner.realm, "Fixture parent", [owner], "", acting_user=owner
        )
        first = check_add_user_group(owner.realm, "Fixture first", [peer], "", acting_user=owner)
        second = check_add_user_group(owner.realm, "Fixture second", [other], "", acting_user=owner)
        events = [
            {
                "id": 1,
                "type": "user_group",
                "op": "add_members",
                "group_id": selected.id,
                "user_ids": [other.id, peer.id, peer.id],
            },
            {
                "id": 2,
                "type": "user_group",
                "op": "add_subgroups",
                "group_id": selected.id,
                "direct_subgroup_ids": [second.id, first.id, second.id],
            },
            {
                "id": 3,
                "type": "user_group",
                "op": "remove_members",
                "group_id": selected.id,
                "user_ids": [other.id],
            },
            {
                "id": 4,
                "type": "user_group",
                "op": "remove_subgroups",
                "group_id": selected.id,
                "direct_subgroup_ids": [second.id],
            },
        ]
        params = {"event_types": orjson.dumps(["realm_user_groups", "user_group"]).decode()}
        queue = EventQueueData(queue_id="15:21", idle_queue_timeout_secs=600)
        self.login_user(owner)

        def request(session: bool, *, replay: bool = True) -> tuple[int, bytes]:
            with stub_event_queue_user_events(queue, events if replay else []):
                response = (
                    self.client_post("/json/register", params)
                    if session
                    else self.api_post(owner, "/api/v1/register", params)
                )
            self.assert_json_success(response)
            return response.status_code, response.content

        def groups(content: bytes) -> dict[int, dict[str, Any]]:
            return {row["id"]: row for row in orjson.loads(content)["realm_user_groups"]}

        appended: list[list[int]] = []
        separated: list[list[int]] = []

        def witness_append(old: list[int], new: list[int]) -> bool:
            before = old.copy()
            result: bool = append(old, new)
            self.assertTrue(result)
            self.assertEqual(old, sorted(before + new))
            appended.append(old.copy())
            return result

        def witness_subtract(old: list[int], new: list[int]) -> list[int]:
            before = old.copy()
            result: list[int] = subtract(old, new)
            self.assertEqual(result, sorted(set(before) - set(new)))
            self.assertIsNot(result, old)
            self.assertEqual(old, before)
            separated.append(result)
            return result

        def reject_append(old: list[int], new: list[int]) -> bool:
            before = old.copy()
            self.assertFalse(append(old, [*new, True]))
            self.assertEqual(old, before)
            return False

        def reject_subtract(old: list[int], new: list[int]) -> None:
            before = old.copy()
            self.assertIsNone(subtract(old, [*new, True]))
            self.assertEqual(old, before)

        with time_machine.travel(timezone_now() + timedelta(seconds=1), tick=False):
            for session in (False, True):
                with self.subTest(session=session):
                    initial = request(session, replay=False)
                    before = groups(initial[1])
                    self.assertEqual(before[selected.id]["members"], [owner.id])
                    self.assertEqual(before[selected.id]["direct_subgroup_ids"], [])
                    with patch.object(native_group_payload, "_roster_replay_backend", None):
                        expected = request(session)
                    self.assertEqual(orjson.loads(expected[1])["last_event_id"], 4)
                    after = groups(expected[1])
                    self.assertEqual(after[selected.id]["members"], sorted([owner.id, peer.id]))
                    self.assertEqual(after[selected.id]["direct_subgroup_ids"], [first.id])
                    self.assertEqual(after[first.id], before[first.id])
                    self.assertEqual(after[second.id], before[second.id])

                    appended.clear()
                    separated.clear()
                    with (
                        patch.object(
                            backend,
                            "append_and_sort_register_group_ids",
                            side_effect=witness_append,
                        ),
                        patch.object(
                            backend, "subtract_register_group_ids", side_effect=witness_subtract
                        ),
                    ):
                        self.assertEqual(request(session), expected)
                    self.assertEqual(
                        appended,
                        [
                            sorted([owner.id, other.id, peer.id, peer.id]),
                            sorted([second.id, first.id, second.id]),
                        ]
                        if enabled
                        else [],
                    )
                    self.assertEqual(
                        separated, [sorted([owner.id, peer.id]), [first.id]] if enabled else []
                    )

                    decline_append = Mock(side_effect=reject_append)
                    decline_remove = Mock(side_effect=reject_subtract)
                    for candidate in (
                        SimpleNamespace(
                            assemble_group_membership=backend.assemble_group_membership
                        ),
                        SimpleNamespace(
                            append_and_sort_register_group_ids=decline_append,
                            subtract_register_group_ids=decline_remove,
                        ),
                    ):
                        with patch.object(
                            native_group_payload, "_roster_replay_backend", candidate
                        ):
                            self.assertEqual(request(session), expected)
                    self.assertEqual(decline_append.call_count, 2)
                    self.assertEqual(decline_remove.call_count, 2)
                    self.assertEqual(request(session, replay=False), initial)
