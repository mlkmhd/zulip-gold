# ruff: noqa: INP001
"""Compare the group response with the exact Python membership and payload logic."""

import ast
import json
import os
import random
import unittest
from dataclasses import dataclass
from importlib import util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
FIELDS = (
    "can_add_members_group",
    "can_join_group",
    "can_leave_group",
    "can_manage_group",
    "can_mention_group",
    "can_remove_members_group",
)


@dataclass
class Members:
    direct_members: list[int]
    direct_subgroups: list[int]


@dataclass
class RealmGroups:
    api_groups: list[dict[str, object]]
    system_groups_name_dict: dict[int, str]
    anonymous_group_membership: dict[int, dict[str, list[int]]]


def load_bridge(*, enabled: bool) -> ModuleType:
    path = ROOT / "zerver/lib/native_group_payload.py"
    spec = util.spec_from_file_location("native_group_payload", path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    with patch.dict(os.environ, {"ZULIP_RUST_GROUP_PAYLOAD": str(int(enabled))}):
        spec.loader.exec_module(module)
    return module


def timestamp(value: int) -> int:
    return value + 4


def load_reference() -> ModuleType:
    default = ROOT / "zerver/lib/user_groups.py"
    path = Path(os.environ.get("ZULIP_GROUP_REFERENCE", default))
    tree = ast.parse(path.read_text(), filename=str(path))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    membership = functions["get_members_and_subgroups_of_groups"].body
    first = next(
        index
        for index, node in enumerate(membership)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "group_members_dict"
    )
    second = next(
        index
        for index, node in enumerate(membership)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "all_members"
    )
    payload = functions["user_groups_in_realm_serialized"].body
    start = next(
        index
        for index, node in enumerate(payload)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "group_dicts"
    )
    parts = ast.parse(
        "def assemble_members(group_ids, all_members):\n    pass\n"
        "def assemble_groups(realm_groups, realm_group_ids, group_members_dict, anonymous_group_ids):\n"
        "    pass\n"
    )
    members_fn, payload_fn = parts.body
    assert isinstance(members_fn, ast.FunctionDef) and isinstance(payload_fn, ast.FunctionDef)
    members_fn.body = membership[first : first + 2] + membership[second:]
    payload_fn.body = payload[start:]
    parts.body.append(functions["get_setting_value_for_user_group_object"])
    module = ModuleType("python_group_payload")
    vars(module).update(
        UserGroupMembersData=Members,
        UserGroupMembersDict=dict,
        RealmUserGroupsData=RealmGroups,
        UserGroupDict=dict,
        datetime_to_timestamp=timestamp,
    )
    # Only compile the Python response logic; do not run its Django queries.
    exec(compile(ast.fix_missing_locations(parts), str(path), "exec"), vars(module))  # noqa: S102
    return module


class GroupPayloadParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = load_bridge(enabled=True)
        assert cls.bridge.membership_available()
        cls.reference = load_reference()

    def test_seeded_membership_and_realm_payloads(self) -> None:
        rng = random.Random(987334)
        for _ in range(3000):
            groups = rng.sample(range(70), rng.randrange(1, 14))
            selected = groups[: rng.randrange(len(groups) + 1)]
            anonymous = set(groups) - set(selected)
            rows = [
                (rng.choice(("user", "group")), rng.choice(groups), rng.randrange(35))
                for _ in range(rng.randrange(32))
            ]
            expected_members = self.reference.assemble_members(groups, rows)
            native_members = self.bridge.assemble_membership(groups, rows, Members)
            self.assertIsNotNone(native_members)
            self.assertEqual(list(native_members.items()), list(expected_members.items()))
            named = [
                SimpleNamespace(
                    id=group,
                    name="گروه نمونه",
                    creator_id=rng.choice((None, 1)),
                    date_created=rng.choice((None, 1024)),
                    description="نمونه",
                    is_system_group=bool(rng.getrandbits(1)),
                    deactivated=bool(rng.getrandbits(1)),
                    **{f"{field}_id": rng.choice(groups) for field in FIELDS},
                )
                for group in selected
            ]
            expected = self.reference.assemble_groups(
                named, set(selected), expected_members, anonymous
            )
            result = self.bridge.assemble_group_payload(
                named, set(selected), native_members, anonymous, timestamp
            )
            self.assertIsNotNone(result)
            self.assertEqual(
                json.dumps(result),
                json.dumps(
                    (
                        expected.api_groups,
                        expected.system_groups_name_dict,
                        expected.anonymous_group_membership,
                    )
                ),
            )

    def test_membership_alias_and_old_extension_fallback(self) -> None:
        members = Members([2], [14])
        raw = {1: members, 3: Members([8], [10])}
        group = SimpleNamespace(
            id=3,
            name="name",
            creator_id=None,
            date_created=None,
            description="",
            is_system_group=False,
            deactivated=False,
            **{f"{field}_id": 1 for field in FIELDS},
        )
        result = self.bridge.assemble_group_payload([group], {3}, raw, {1}, timestamp)
        self.assertIsNotNone(result)
        self.assertIs(result[0][0]["can_join_group"]["direct_members"], members.direct_members)
        self.assertIs(result[2][1]["direct_subgroups"], members.direct_subgroups)
        self.assertIsNone(self.bridge.assemble_membership([1], [("group", 3, 4)], Members))
        self.assertIsNone(self.bridge.assemble_membership([2**70], [], Members))
        with patch.object(self.bridge, "_backend", SimpleNamespace()):
            self.assertFalse(self.bridge.membership_available())
            self.assertIsNone(self.bridge.assemble_membership([1], [], Members))
            self.assertIsNone(self.bridge.assemble_group_payload([], set(), {}, set(), timestamp))
