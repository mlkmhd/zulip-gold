# Documented in https://zulip.readthedocs.io/en/latest/subsystems/queuing.html
import logging
from typing import Any, cast

from django.conf import settings
from django.db import connection
from psycopg2.sql import SQL, Literal
from typing_extensions import override

from zerver.lib.native_backend import load_native_contract
from zerver.worker.base import LoopQueueProcessingWorker, assign_queue

logger = logging.getLogger(__name__)


_activity_backend = load_native_contract(
    "ZULIP_RUST_PREPARED_ACTIVITY_BATCH", "PREPARED_ACTIVITY_BATCH_API"
)

_activity_upsert_backend = load_native_contract(
    "ZULIP_RUST_PREPARED_ACTIVITY_UPSERT_VALUES", "PREPARED_ACTIVITY_UPSERT_VALUES_API"
)
_ACTIVITY_UPSERT = """
            INSERT INTO zerver_useractivity(user_profile_id, client_id, query, count, last_visit)
            VALUES {rows}
            ON CONFLICT (user_profile_id, client_id, query) DO UPDATE SET
                count = zerver_useractivity.count + excluded.count,
                last_visit = greatest(zerver_useractivity.last_visit, excluded.last_visit)
            """

ActivityRow = tuple[int, int, str, int, float]


def _activity_rows(events: list[dict[str, Any]]) -> list[ActivityRow]:
    native = getattr(_activity_backend, "group_prepared_user_activity", None)
    if callable(native):
        prepared = cast("list[ActivityRow] | None", native(events))
        if prepared is not None:
            return prepared

    # Keep the original deduplication, including order and fallback input types.
    pending: dict[tuple[int, int, str], tuple[int, float]] = {}
    for event in events:
        user_profile_id = event["user_profile_id"]
        client_id = event["client_id"]
        key = (user_profile_id, client_id, event["query"])
        if key not in pending:
            pending[key] = (1, event["time"])
        else:
            count, event_time = pending[key]
            pending[key] = (count + 1, max(event_time, event["time"]))
    return [(*key, count, event_time) for key, (count, event_time) in pending.items()]


@assign_queue("user_activity")
class UserActivityWorker(LoopQueueProcessingWorker):
    """The UserActivity queue is perhaps our highest-traffic queue, and
    requires some care to ensure it performs adequately.

    We use a LoopQueueProcessingWorker as a performance optimization
    for managing the queue.  The structure of UserActivity records is
    such that they are easily deduplicated before being sent to the
    database; we take advantage of that to make this queue highly
    effective at dealing with a backlog containing many similar
    events.  Such a backlog happen in a few ways:

    * In abuse/DoS situations, if a client is sending huge numbers of
      similar requests to the server.
    * If the queue ends up with several minutes of backlog e.g. due to
      downtime of the queue processor, many clients will have several
      common events from doing an action multiple times.

    """

    client_id_map: dict[str, int] = {}

    @override
    def __init__(
        self,
        threaded: bool = False,
        disable_timeout: bool = False,
        worker_num: int | None = None,
    ) -> None:
        if settings.USER_ACTIVITY_SHARDS > 1 and worker_num is not None:  # nocoverage
            self.queue_name += f"_shard{worker_num}"
        super().__init__(threaded, disable_timeout, worker_num)

    @override
    def start(self) -> None:
        # For our unit tests to make sense, we need to clear this on startup.
        self.client_id_map = {}
        super().start()

    @override
    def consume_batch(self, user_activity_events: list[dict[str, Any]]) -> None:
        activity_rows = _activity_rows(user_activity_events)
        plan_values = getattr(_activity_upsert_backend, "plan_prepared_activity_values", None)
        if callable(plan_values):
            plan = cast(
                "tuple[str, tuple[int | float | str, ...]] | None", plan_values(activity_rows)
            )
            if plan is not None:
                values, parameters = plan
                with connection.cursor() as cursor:
                    cursor.execute(_ACTIVITY_UPSERT.format(rows=values), parameters)
                return

        rows = []
        for user_profile_id, client_id, query, count, event_time in activity_rows:
            rows.append(
                SQL("({},{},{},{},to_timestamp({}))").format(
                    Literal(user_profile_id),
                    Literal(client_id),
                    Literal(query),
                    Literal(count),
                    Literal(event_time),
                )
            )

        # Perform a single bulk UPSERT for all of the rows
        sql_query = SQL(_ACTIVITY_UPSERT).format(rows=SQL(", ").join(rows))
        with connection.cursor() as cursor:
            cursor.execute(sql_query)
