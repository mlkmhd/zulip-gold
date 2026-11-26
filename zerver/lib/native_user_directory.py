"""Native assembly of user cards already prepared by Django."""

from __future__ import annotations

import os
from importlib import import_module
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from zerver.lib.native_backend import load_native_contract

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


_legacy_requested = os.environ.get("ZULIP_RUST_USER_DIRECTORY") == "1"
_prepared_backend = (
    None
    if _legacy_requested or os.environ.get("ZULIP_RUST_PREPARED_USER_DIRECTORY") == "0"
    else load_native_contract("ZULIP_RUST_PREPARED_USER_DIRECTORY", "PREPARED_USER_DIRECTORY_API")
)

try:
    _backend = import_module("_zulip_backend") if _legacy_requested else None
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def _assembler() -> object:
    return getattr(
        _backend if _backend is not None else _prepared_backend, "assemble_user_directory", None
    )


def available() -> bool:
    return callable(_assembler())


def assemble(rows: list[UserRowPresentation]) -> dict[int, APIUserDict] | None:
    native = _assembler()
    if not callable(native):
        return None
    if _backend is None and (
        type(rows) is not list or any(type(row) is not UserRowPresentation for row in rows)
    ):
        return None
    return cast("dict[int, APIUserDict] | None", native(rows))
