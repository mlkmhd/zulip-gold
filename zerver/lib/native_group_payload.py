"""Opt-in projection of group and membership rows already selected by Django."""

from __future__ import annotations

import os
from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from datetime import datetime

    from zerver.lib.types import UserGroupMembersData, UserGroupMembersDict
    from zerver.lib.user_groups import UserGroupDict
    from zerver.models import NamedUserGroup

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_GROUP_PAYLOAD") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def membership_available() -> bool:
    return getattr(_backend, "assemble_group_membership", None) is not None


def assemble_membership(
    groups: list[int], rows: list[tuple[str, int, int]], members_model: type[UserGroupMembersData]
) -> dict[int, UserGroupMembersData] | None:
    native = getattr(_backend, "assemble_group_membership", None)
    if native is None:
        return None
    return cast("dict[int, UserGroupMembersData] | None", native(groups, rows, members_model))


def assemble_group_payload(
    selected: list[NamedUserGroup],
    named_ids: set[int],
    by_id: dict[int, UserGroupMembersData],
    anonymous_ids: set[int],
    timestamp: Callable[[datetime], int],
) -> tuple[list[UserGroupDict], dict[int, str], dict[int, UserGroupMembersDict]] | None:
    native = getattr(_backend, "assemble_realm_group_payload", None)
    if native is None:
        return None
    return cast(
        "tuple[list[UserGroupDict], dict[int, str], dict[int, UserGroupMembersDict]] | None",
        native(selected, named_ids, by_id, anonymous_ids, timestamp),
    )
