"""Opt-in channel subscription and activity shaping for rows selected by Django."""

from __future__ import annotations

import os
from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from zerver.lib.types import APIStreamDict, RawSubscriptionDict, SubscriptionStreamDict

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_CHANNEL_PAYLOAD") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def assemble_subscription(
    subscription: RawSubscriptionDict, stream: APIStreamDict
) -> SubscriptionStreamDict | None:
    native = getattr(_backend, "assemble_channel_subscription", None)
    if native is None:
        return None
    return cast("SubscriptionStreamDict | None", native(subscription, stream))


def average_weekly_traffic(age_days: int, count: int) -> tuple[int | None] | None:
    native = getattr(_backend, "calculate_weekly_channel_traffic", None)
    if native is None:
        return None
    return cast("tuple[int | None] | None", native(age_days, count))
