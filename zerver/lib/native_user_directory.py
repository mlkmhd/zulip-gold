"""Optional native assembly of user cards already prepared by Django."""

from __future__ import annotations

import os
from importlib import import_module
from typing import TYPE_CHECKING, Any, NamedTuple, cast

if TYPE_CHECKING:
    from zerver.lib.users import APIUserDict


class UserRowPresentation(NamedTuple):
    email: str
    user_id: int
    avatar_source: str
    avatar_version: int
    is_admin: bool
    is_owner: bool
    is_guest: bool
    role: int
    is_bot: bool
    full_name: str
    timezone: str | None
    is_active: bool
    date_joined: str
    delivery_email: str | None
    is_imported_stub: bool
    is_deleted: bool
    avatar: tuple[str | None] | None
    bot: tuple[int | None, bool, int | None] | None
    profile_data: dict[str, Any] | None


try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_USER_DIRECTORY") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def available() -> bool:
    return callable(getattr(_backend, "assemble_user_directory", None))


def assemble(rows: list[UserRowPresentation]) -> dict[int, APIUserDict] | None:
    native = getattr(_backend, "assemble_user_directory", None)
    if not callable(native):
        return None
    return cast("dict[int, APIUserDict] | None", native(rows))
