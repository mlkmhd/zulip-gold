"""Optional persistence of an existing Django user's direct-message pin."""

import os
from importlib import import_module
from typing import Literal

from django.db import DEFAULT_DB_ALIAS, DatabaseError, connections, router
from django.db.models import Model
from django.db.models.signals import post_save, pre_save

from zerver.models import Subscription, UserProfile

try:
    _backend = (
        import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_DM_PIN") == "1" else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None

PinResult = Literal["changed", "unchanged", "missing_conversation"]


def set_existing_pin(
    user_profile: UserProfile, member_ids: set[int], pinned: object
) -> PinResult | None:
    apply = getattr(_backend, "set_existing_dm_pin", None)
    if not callable(apply):
        return None
    if (
        type(pinned) is not bool
        or user_profile._state.db != DEFAULT_DB_ALIAS
        or router.routers
        or Subscription.save is not Model.save
        or pre_save.has_listeners(Subscription)
        or post_save.has_listeners(Subscription)
        or not all(type(id) is int and -(2**31) <= id < 2**31 for id in member_ids)
    ):
        return None
    connection = connections[DEFAULT_DB_ALIAS]
    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        return None
    with connection.cursor() as cursor:
        result = apply(cursor, user_profile.id, list(member_ids), pinned)
        if result == "changed":
            return "changed"
        if result == "unchanged":
            return "unchanged"
        if result == "missing_conversation":
            return "missing_conversation"
        if result == "missing_subscription":
            raise Subscription.DoesNotExist("Subscription matching query does not exist.")
        if result == "not_updated":
            raise DatabaseError("Save with update_fields did not affect any rows.")
        # A call that may have written must not be retried through Django.
        raise RuntimeError("Unexpected native direct-message pin result")
