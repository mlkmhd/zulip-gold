from datetime import datetime, timedelta

from analytics.lib.counts import CountStat
from zerver.lib.native_backend import load_native_contract
from zerver.lib.timestamp import floor_to_day, floor_to_hour, verify_UTC

try:
    _rust_time_grid = load_native_contract(
        "ZULIP_RUST_ANALYTICS_TIME_BUCKETS", "ANALYTICS_TIME_BUCKETS_API"
    )
except ImportError:
    _rust_time_grid = None
_walk_prepared_analytics_time_grid = getattr(
    _rust_time_grid, "walk_prepared_analytics_time_grid", None
)


# If min_length is None, returns end_times from ceiling(start) to floor(end), inclusive.
# If min_length is greater than 0, pads the list to the left.
# So informally, time_range(Sep 20, Sep 22, day, None) returns [Sep 20, Sep 21, Sep 22],
# and time_range(Sep 20, Sep 22, day, 5) returns [Sep 18, Sep 19, Sep 20, Sep 21, Sep 22]
def time_range(
    start: datetime, end: datetime, frequency: str, min_length: int | None
) -> list[datetime]:
    verify_UTC(start)
    verify_UTC(end)
    if frequency == CountStat.HOUR:
        end = floor_to_hour(end)
        step = timedelta(hours=1)
    elif frequency == CountStat.DAY:
        end = floor_to_day(end)
        step = timedelta(days=1)
    else:
        raise AssertionError(f"Unknown frequency: {frequency}")

    if min_length is not None:
        start = min(start, end - (min_length - 1) * step)
    if _walk_prepared_analytics_time_grid is not None:
        return _walk_prepared_analytics_time_grid(start, end, step)

    times = []
    current = end
    while current >= start:
        times.append(current)
        current -= step
    times.reverse()
    return times
