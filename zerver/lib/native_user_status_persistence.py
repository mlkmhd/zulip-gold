"""Optional persistence of existing statuses using Django's transaction."""

import os
from datetime import datetime
from importlib import import_module
from typing import cast

from django.db import connections, router, transaction
from django.db.models import Model
from django.db.models.signals import post_save, pre_save

from zerver.models import UserStatus

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_USER_STATUS_PERSISTENCE") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None

StatusChanges = tuple[str | None, str | None, str | None, str | None]


def update_existing_status(
    user_id: int, client_id: int, edited_at: datetime, changes: StatusChanges
) -> bool:
    update = getattr(_backend, "update_existing_user_status", None)
    if not callable(update):
        return False
    if (
        UserStatus.save is not Model.save
        or pre_save.has_listeners(UserStatus)
        or post_save.has_listeners(UserStatus)
    ):
        # Keep the full ORM path for deployments with model hooks.
        return False
    if any(
        type(value) is not int or not (-(2**31) <= value < 2**31) for value in (user_id, client_id)
    ) or any(value is not None and type(value) is not str for value in changes):
        return False
    database = router.db_for_write(UserStatus)
    connection = connections[database]
    if connection.vendor != "postgresql":
        return False
    with transaction.atomic(using=database), connection.cursor() as cursor:
        # SQL errors propagate, never rerun an attempted write. Only no row
        # matched can fall back to the existing creation and race handling.
        return cast(bool, update(cursor, user_id, client_id, edited_at, changes))
