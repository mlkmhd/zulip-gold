"""Optional batch persistence of a Django-validated folder reorder."""

import os
from importlib import import_module

from django.db import DEFAULT_DB_ALIAS, DatabaseError, connections, router
from django.db.models import Model
from django.db.models.signals import post_save, pre_save

from zerver.models.channel_folders import ChannelFolder

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_CHANNEL_FOLDER_ORDER") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def update_order(
    realm_id: int,
    folders: list[ChannelFolder],
    order_mapping: dict[int, int],
) -> bool:
    update = getattr(_backend, "update_channel_folder_order", None)
    if not callable(update) or len(folders) < 2:
        return False
    if (
        ChannelFolder.save is not Model.save
        or pre_save.has_listeners(ChannelFolder)
        or post_save.has_listeners(ChannelFolder)
        or not all(
            folder._state.db == DEFAULT_DB_ALIAS
            and router.db_for_write(ChannelFolder, instance=folder) == DEFAULT_DB_ALIAS
            for folder in folders
        )
    ):
        return False
    connection = connections[DEFAULT_DB_ALIAS]
    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        return False
    changes = [(folder.id, order_mapping[folder.id]) for folder in folders]
    if any(position >= 2**31 for _, position in changes):
        return False
    with connection.cursor() as cursor:
        updated = update(cursor, realm_id, changes)
        if updated != len(changes):
            # Never replay an attempted write. A row being deleted after
            # selection fails and rolls back like Model.save(update_fields=...).
            raise DatabaseError("Save with update_fields did not affect any rows.")
    return True
