"""Opt-in channel subscription and activity shaping for rows selected by Django."""

from __future__ import annotations

import os
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, TypeAlias, cast

if TYPE_CHECKING:
    from zerver.lib.types import APIStreamDict, RawSubscriptionDict, SubscriptionStreamDict

OwnSubscriptionInput: TypeAlias = tuple["RawSubscriptionDict", "APIStreamDict", int | None]


def _load_backend(flag: str) -> ModuleType | None:
    if os.environ.get(flag) != "1":
        return None
    try:
        return import_module("_zulip_backend")
    except ModuleNotFoundError as error:
        if error.name != "_zulip_backend":
            raise
        return None


_backend = _load_backend("ZULIP_RUST_CHANNEL_PAYLOAD")
_own_backend = _load_backend("ZULIP_RUST_OWN_SUBSCRIPTIONS")


def own_subscriptions_available() -> bool:
    return callable(getattr(_own_backend, "assemble_own_subscription_batch", None))


def assemble_own_subscriptions(
    rows: list[OwnSubscriptionInput], *, partial: bool, threshold: int | None
) -> tuple[list[SubscriptionStreamDict], list[int]] | None:
    native = getattr(_own_backend, "assemble_own_subscription_batch", None)
    if native is None:
        return None
    return cast(
        "tuple[list[SubscriptionStreamDict], list[int]] | None",
        native(rows, partial, threshold),
    )


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
