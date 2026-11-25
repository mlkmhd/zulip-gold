import os
from importlib import import_module
from unittest import TestCase
from unittest.mock import patch

from typing_extensions import override

from zerver.lib import display_recipient
from zerver.lib.native_backend import supports_default_rust_backend
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.types import UserDisplayRecipient
from zerver.models import Recipient
from zerver.models.recipients import get_or_create_direct_message_group

TURN = {(900, 2, 21), (600, 2, 18)}


def fake_record(member_id: int) -> UserDisplayRecipient:
    return {
        "id": member_id,
        "email": "synthetic@example.invalid",
        "full_name": f"Synthetic {member_id}",
        "is_mirror_dummy": False,
    }


class DisplayRecipientOrderRustTest(TestCase):
    def test_native_indices_stay_inside_each_synthetic_group(self) -> None:
        try:
            planner = import_module("_zulip_backend").plan_display_recipient_order
        except (ImportError, AttributeError):
            self.skipTest("Rust display recipient ordering is not available")

        low, high = -(2**63), 2**63 - 1
        self.assertEqual(planner([(high, -9, low), (), (1,), (9, 8)]), [[2, 1, 0], [], [0], [1, 0]])
        self.assertEqual(planner([]), [])
        for groups in (((),), [[10]], [(True,)], [(2**64,)], [(11, 11)], [(None,)]):
            self.assertIsNone(planner(groups))

    def test_actual_projection_preserves_all_read_order_and_record_aliases(self) -> None:
        trace: list[tuple[str, object]] = []
        encounter = [row[0] for row in TURN]

        class Groups(dict[int, set[int]]):
            @override
            def __getitem__(self, key: int) -> set[int]:
                trace.append(("group", key))
                return super().__getitem__(key)

        class Records(dict[int, UserDisplayRecipient]):
            @override
            def __getitem__(self, key: int) -> UserDisplayRecipient:
                trace.append(("record", key))
                return super().__getitem__(key)

        def fetched_groups(keys: list[int]) -> dict[int, set[int]]:
            trace.append(("groups_query", keys))
            return Groups({encounter[0]: {7, 2}})

        def fetched_records(keys: list[int]) -> dict[int, UserDisplayRecipient]:
            trace.append(("records_query", keys))
            return records

        records = Records({key: fake_record(key) for key in (2, 7, 9)})
        with (
            patch(
                "zerver.models.recipients.bulk_get_direct_message_group_user_ids",
                side_effect=fetched_groups,
            ),
            patch.object(
                display_recipient,
                "bulk_fetch_single_user_display_recipients",
                side_effect=fetched_records,
            ),
        ):
            original_planner = display_recipient._rust_plan_display_recipient_order
            with patch.object(display_recipient, "_rust_plan_display_recipient_order", None):
                old = display_recipient.bulk_fetch_user_display_recipients(
                    TURN, {encounter[1]: {9, 2}}
                )
            before = trace.copy()
            trace.clear()
            with patch.object(
                display_recipient, "_rust_plan_display_recipient_order", wraps=original_planner
            ) as spy:
                if original_planner is not None:
                    got = display_recipient.bulk_fetch_user_display_recipients(
                        TURN, {encounter[1]: {9, 2}}
                    )
                    groups = spy.call_args.args[0]
                    self.assertEqual([set(group) for group in groups], [{2, 7}, {2, 9}])
                    spy.assert_called_once()
                else:
                    with patch.object(
                        display_recipient, "_rust_plan_display_recipient_order", None
                    ):
                        got = display_recipient.bulk_fetch_user_display_recipients(
                            TURN, {encounter[1]: {9, 2}}
                        )
                    spy.assert_not_called()
        self.assertEqual(trace, before)
        self.assertEqual(trace[0], ("groups_query", [encounter[0]]))
        self.assertEqual(list(got), encounter)
        self.assertEqual(old, got)
        self.assertIs(got[encounter[0]][0], got[encounter[1]][0])
        self.assertIs(got[encounter[0]][0], dict.__getitem__(records, 2))

    def test_missing_display_record_and_group_raise_original_key_error(self) -> None:
        trace: list[int] = []

        class Records(dict[int, UserDisplayRecipient]):
            @override
            def __getitem__(self, key: int) -> UserDisplayRecipient:
                trace.append(key)
                return super().__getitem__(key)

        loaded = Records({3: fake_record(3), 9: fake_record(9)})
        planner = display_recipient._rust_plan_display_recipient_order
        for flag in (None, planner):
            trace.clear()
            with (
                patch.object(display_recipient, "_rust_plan_display_recipient_order", flag),
                patch(
                    "zerver.models.recipients.bulk_get_direct_message_group_user_ids",
                    return_value={6: {5, 9, 3}},
                ),
                patch.object(
                    display_recipient,
                    "bulk_fetch_single_user_display_recipients",
                    return_value=loaded,
                ),
                self.assertRaises(KeyError) as missing,
            ):
                display_recipient.bulk_fetch_user_display_recipients({(6, 2, 0)})
            self.assertEqual(missing.exception.args, (5,))
            self.assertEqual(trace, [3, 5])

        with (
            patch(
                "zerver.models.recipients.bulk_get_direct_message_group_user_ids", return_value={}
            ),
            patch.object(display_recipient, "bulk_fetch_single_user_display_recipients") as fetch,
            self.assertRaises(KeyError) as missing_group,
        ):
            display_recipient.bulk_fetch_user_display_recipients({(6, 2, 0)})
        self.assertEqual(missing_group.exception.args, (6,))
        fetch.assert_not_called()

    def test_malformed_or_nonpermutation_plan_cannot_change_membership(self) -> None:
        group = {2, 7}
        records = {key: fake_record(key) for key in group}
        malformed_plans: tuple[object, ...] = (
            None,
            (),
            [[0, 0]],
            [[-1, 0]],
            [[2, 0]],
            [[True, 0]],
            [[], []],
        )
        for incompatible in malformed_plans:
            with (
                patch.object(
                    display_recipient,
                    "_rust_plan_display_recipient_order",
                    return_value=incompatible,
                ),
                patch(
                    "zerver.models.recipients.bulk_get_direct_message_group_user_ids",
                    return_value={6: group},
                ),
                patch.object(
                    display_recipient,
                    "bulk_fetch_single_user_display_recipients",
                    return_value=records,
                ),
            ):
                got = display_recipient.bulk_fetch_user_display_recipients({(6, 2, 0)})
            self.assertEqual(got, {6: [records[2], records[7]]})
            self.assertIs(got[6][0], records[2])

    def test_changed_group_uses_original_sorted_lookup(self) -> None:
        encounter = [row[0] for row in TURN]
        memberships = {encounter[0]: {4}, encounter[1]: {3, 8}}

        class Records(dict[int, UserDisplayRecipient]):
            @override
            def __getitem__(self, key: int) -> UserDisplayRecipient:
                if key == 4:
                    memberships[encounter[1]].remove(8)
                    memberships[encounter[1]].add(7)
                return super().__getitem__(key)

        def plan(groups: list[tuple[int, ...]]) -> list[list[int]]:
            return [sorted(range(len(group)), key=group.__getitem__) for group in groups]

        records = Records({key: fake_record(key) for key in (3, 4, 7, 8)})
        with (
            patch.object(
                display_recipient, "_rust_plan_display_recipient_order", side_effect=plan
            ) as spy,
            patch(
                "zerver.models.recipients.bulk_get_direct_message_group_user_ids",
                return_value=memberships,
            ),
            patch.object(
                display_recipient, "bulk_fetch_single_user_display_recipients", return_value=records
            ),
        ):
            got = display_recipient.bulk_fetch_user_display_recipients(TURN)
        spy.assert_called_once()
        self.assertEqual([record["id"] for record in got[encounter[1]]], [3, 7])
        self.assertIs(got[encounter[1]][1], dict.__getitem__(records, 7))

    def test_legacy_set_subclass_and_arbitrary_precision_fallback(self) -> None:
        class Derived(set[int]):
            pass

        with patch.object(display_recipient, "_rust_plan_display_recipient_order") as spy:
            self.assertIsNone(display_recipient._plan_display_recipient_order([Derived({2, 1})]))
            spy.assert_not_called()

        large_id = 2**90
        with (
            patch(
                "zerver.models.recipients.bulk_get_direct_message_group_user_ids",
                return_value={6: {large_id, 1}},
            ),
            patch.object(
                display_recipient,
                "bulk_fetch_single_user_display_recipients",
                return_value={key: fake_record(key) for key in (large_id, 1)},
            ),
        ):
            got = display_recipient.bulk_fetch_user_display_recipients({(6, 2, 0)})
        self.assertEqual([record["id"] for record in got[6]], [1, large_id])


class DisplayRecipientOrderNativeGroupTest(ZulipTestCase):
    def test_selected_groups_keep_their_member_sets_and_increasing_id_order(self) -> None:
        if not supports_default_rust_backend() or os.environ.get(
            "ZULIP_RUST_DISPLAY_RECIPIENT_ORDER"
        ):
            self.skipTest("No-flag startup is tested on Ubuntu 24.04 x86-64")
        if (
            getattr(
                display_recipient._rust_display_recipient_order, "DISPLAY_RECIPIENT_ORDER_API", None
            )
            != 1
        ):
            self.skipTest("Wheel predates the default recipient-order contract")

        a, b, c, d = (
            self.example_user(name).id for name in ("hamlet", "iago", "othello", "cordelia")
        )
        first_members = [a, c, b]
        second_members = [d, b, a]
        first = get_or_create_direct_message_group(first_members)
        second = get_or_create_direct_message_group(second_members)
        selectors = {
            (first.recipient_id, Recipient.DIRECT_MESSAGE_GROUP, first.id),
            (second.recipient_id, Recipient.DIRECT_MESSAGE_GROUP, second.id),
        }
        native = display_recipient._rust_plan_display_recipient_order
        assert native is not None
        with patch.object(
            display_recipient, "_rust_plan_display_recipient_order", wraps=native
        ) as planner:
            result = display_recipient.bulk_fetch_user_display_recipients(selectors)
        planner.assert_called_once()
        self.assertCountEqual(
            [frozenset(group) for group in planner.call_args.args[0]],
            [frozenset(first_members), frozenset(second_members)],
        )
        self.assertEqual(set(result), {first.recipient_id, second.recipient_id})
        for key, members in (
            (first.recipient_id, first_members),
            (second.recipient_id, second_members),
        ):
            self.assertEqual([row["id"] for row in result[key]], sorted(members))
