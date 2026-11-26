from datetime import datetime, timezone
from typing import cast

from zerver.lib.native_backend import load_native_contract
from zerver.lib.queue import queue_json_publish_rollback_unsafe
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.models import UserActivityInterval, UserProfile

_interval_backend = load_native_contract(
    "ZULIP_RUST_PREPARED_ACTIVITY_INTERVAL", "PREPARED_ACTIVITY_INTERVAL_API"
)


def _plan_activity_interval(
    old_start: datetime, old_end: datetime, new_start: datetime, new_end: datetime
) -> tuple[bool, datetime, datetime]:
    native = getattr(_interval_backend, "plan_prepared_activity_interval", None)
    if callable(native):
        prepared = cast(
            "tuple[bool, datetime, datetime] | None",
            native(old_start, old_end, new_start, new_end, (datetime, timezone.utc)),
        )
        if prepared is not None:
            return prepared

    if new_start <= old_end and new_end >= old_start:
        end = max(old_end, new_end)
        return True, min(old_start, new_start), end
    return False, new_start, new_end


def do_update_user_activity_interval(user_profile: UserProfile, log_time: datetime) -> None:
    effective_end = log_time + UserActivityInterval.MIN_INTERVAL_LENGTH
    # This code isn't perfect, because with various races we might end
    # up creating two overlapping intervals, but that shouldn't happen
    # often, and can be corrected for in post-processing
    last = UserActivityInterval.objects.filter(user_profile=user_profile).order_by("-end").first()
    # Two intervals overlap iff each interval ends after the other
    # begins.  In this case, we just extend the old interval to
    # include the new interval.
    if last is not None:
        overlaps, interval_start, interval_end = _plan_activity_interval(
            last.start, last.end, log_time, effective_end
        )
        if overlaps:
            last.end = interval_end
            last.start = interval_start
            last.save(update_fields=["start", "end"])
            return

    # Otherwise, the intervals don't overlap, so we should make a new one
    UserActivityInterval.objects.create(
        user_profile=user_profile, start=log_time, end=effective_end
    )


def update_user_activity_interval(user_profile: UserProfile, log_time: datetime) -> None:
    event = {"user_profile_id": user_profile.id, "time": datetime_to_timestamp(log_time)}
    queue_json_publish_rollback_unsafe("user_activity_interval", event)
