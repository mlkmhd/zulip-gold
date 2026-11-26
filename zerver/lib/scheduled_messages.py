import os
from datetime import datetime, timedelta, timezone
from importlib import import_module
from typing import Annotated, Literal

from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from pydantic import Json, ValidatorFunctionWrapHandler, WrapValidator

from zerver.lib.exceptions import DeliveryTimeNotInFutureError, JsonableError, ResourceNotFoundError
from zerver.lib.native_backend import load_native_contract
from zerver.lib.partial import partial
from zerver.lib.recipient_parsing import extract_direct_message_recipient_ids, extract_stream_id
from zerver.lib.timestamp import timestamp_to_datetime
from zerver.models import ScheduledMessage, UserProfile
from zerver.models.scheduled_jobs import (
    APIReminderDirectMessageDict,
    APIScheduledDirectMessageDict,
    APIScheduledStreamMessageDict,
)

_rust_scheduled_job_input = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_SCHEDULED_JOB_INPUT") == "1"
    else None
)
_rust_prepared_scheduled_input = load_native_contract(
    "ZULIP_RUST_PREPARED_SCHEDULED_INPUT", "PREPARED_SCHEDULED_INPUT_API"
)
_rust_scheduled_job_timestamp = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_SCHEDULED_JOB_TIMESTAMP") == "1"
    else None
)
_rust_scheduled_delivery_comparison = load_native_contract(
    "ZULIP_RUST_SCHEDULED_DELIVERY_COMPARISON", "SCHEDULED_DELIVERY_COMPARISON_API"
)
_rust_delivery_time_is_future = getattr(
    _rust_scheduled_delivery_comparison, "scheduled_delivery_time_is_future", None
)
_rust_scheduled_recipient_shape = load_native_contract(
    "ZULIP_RUST_SCHEDULED_RECIPIENT_SHAPE", "SCHEDULED_RECIPIENT_SHAPE_API"
)
_rust_scheduled_message_recipient = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_SCHEDULED_MESSAGE_RECIPIENT") == "1"
    else None
)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_REFERENCE_TIMESTAMP_CONVERTER = timestamp_to_datetime


def parse_scheduled_job_argument(
    kind: Literal["integer", "recipients", "boolean"],
    value: object,
    handler: ValidatorFunctionWrapHandler,
) -> object:
    if _rust_scheduled_job_input is not None and isinstance(value, str):
        argument = _rust_scheduled_job_input.parse_scheduled_job_argument(value, kind)
        if argument is not None:
            return argument
    return handler(value)


def _parse_builtin_scheduled_job_argument(
    kind: Literal["integer", "recipients", "boolean"],
    value: object,
    handler: ValidatorFunctionWrapHandler,
) -> object:
    # Only the three exact annotations below use this shortcut.
    if _rust_scheduled_job_input is None and type(value) is str:
        native = getattr(_rust_prepared_scheduled_input, "parse_scheduled_job_argument", None)
        if native is not None:
            argument = native(value, kind)
            if argument is not None:
                return argument
    return parse_scheduled_job_argument(kind, value, handler)


ScheduledJobInteger = Annotated[
    Json[int], WrapValidator(partial(_parse_builtin_scheduled_job_argument, "integer"))
]
ScheduledJobRecipients = Annotated[
    Json[int | list[int]],
    WrapValidator(partial(_parse_builtin_scheduled_job_argument, "recipients")),
]
ScheduledJobBoolean = Annotated[
    Json[bool], WrapValidator(partial(_parse_builtin_scheduled_job_argument, "boolean"))
]


def _microseconds_since_epoch(value: datetime) -> int:
    since_epoch = value - _UNIX_EPOCH
    return (since_epoch.days * 86_400 + since_epoch.seconds) * 1_000_000 + since_epoch.microseconds


def validate_scheduled_delivery_time(timestamp: int) -> datetime:
    if _rust_scheduled_job_timestamp is not None:
        now_microseconds = _microseconds_since_epoch(timezone_now())
        future = _rust_scheduled_job_timestamp.scheduled_delivery_time_is_future(
            timestamp, now_microseconds
        )
        if future is not None:
            if not future:
                raise DeliveryTimeNotInFutureError
            return _UNIX_EPOCH + timedelta(seconds=timestamp)

    convert = timestamp_to_datetime
    deliver_at = convert(timestamp)
    now = timezone_now()
    future = None
    if (
        _rust_delivery_time_is_future is not None
        and convert is _REFERENCE_TIMESTAMP_CONVERTER
        and type(timestamp) is int
        and type(deliver_at) is datetime
        and deliver_at.tzinfo is timezone.utc
        and type(now) is datetime
        and now.tzinfo is timezone.utc
    ):
        future = _rust_delivery_time_is_future(timestamp, _microseconds_since_epoch(now))
    if future is None:
        future = not (deliver_at <= now)
    if not future:
        raise DeliveryTimeNotInFutureError
    return deliver_at


def parse_scheduled_message_recipient(
    message_type: str, to: int | list[int]
) -> tuple[str, list[int]]:
    recipient_type = None
    classify = getattr(
        _rust_scheduled_recipient_shape, "classify_scheduled_message_recipient", None
    )
    if (
        classify is not None
        and type(message_type) is str
        and message_type.isascii()
        and (type(to) is list or type(to) is int)
    ):
        kind, error = classify(message_type, len(to) if type(to) is list else None)
        # The reference extracts recipients and handles errors in its
        # existing order; only ordinary successful shapes are replaced.
        if error is None and kind in ("stream", "private"):
            recipient_type = kind
    if recipient_type is None and _rust_scheduled_message_recipient is not None:
        recipient_type, error = (
            _rust_scheduled_message_recipient.classify_scheduled_message_recipient(
                message_type, len(to) if isinstance(to, list) else None
            )
        )
        if error == "channel_list":
            raise JsonableError(_("Invalid data type for channel ID"))
        if error == "direct_integer":
            raise JsonableError(_("Invalid data type for recipients"))
        if error == "empty_direct_list":
            raise JsonableError(_("Message must have recipients"))
        assert recipient_type in ("stream", "private")
    if recipient_type is None:
        recipient_type = "stream" if message_type in ("channel", "stream") else "private"

    if recipient_type == "stream":
        return "stream", [extract_stream_id(to)]
    # The first invalid user ID reported by Addressee depends on Python's set order.
    return "private", extract_direct_message_recipient_ids(to)


def access_scheduled_message(
    user_profile: UserProfile,
    scheduled_message_id: int,
    lock_message: bool = False,
) -> ScheduledMessage:
    base_query = ScheduledMessage.objects.all()
    if lock_message:
        # Callers that are going to modify the row should take this lock
        # to avoid racing with an in-progress delivery. Only pass
        # lock_message from within a @transaction.atomic block.
        base_query = base_query.select_for_update(no_key=True)
    try:
        return base_query.get(
            id=scheduled_message_id, sender=user_profile, delivery_type=ScheduledMessage.SEND_LATER
        )
    except ScheduledMessage.DoesNotExist:
        raise ResourceNotFoundError(_("Scheduled message does not exist"))


def get_undelivered_scheduled_messages(
    user_profile: UserProfile,
) -> list[APIScheduledDirectMessageDict | APIScheduledStreamMessageDict]:
    scheduled_messages = ScheduledMessage.objects.filter(
        realm_id=user_profile.realm_id,
        sender=user_profile,
        # Notably, we don't require failed=False, since we will want
        # to display those to users.
        delivered=False,
        delivery_type=ScheduledMessage.SEND_LATER,
    ).order_by("scheduled_timestamp")
    scheduled_message_dicts: list[APIScheduledDirectMessageDict | APIScheduledStreamMessageDict] = [
        scheduled_message.to_dict() for scheduled_message in scheduled_messages
    ]
    return scheduled_message_dicts


def get_undelivered_reminders(
    user_profile: UserProfile,
) -> list[APIReminderDirectMessageDict]:
    reminders = ScheduledMessage.objects.filter(
        realm_id=user_profile.realm_id,
        sender=user_profile,
        # Notably, we don't require failed=False, since we will want
        # to display those to users.
        delivered=False,
        delivery_type=ScheduledMessage.REMIND,
    ).order_by("scheduled_timestamp")
    reminder_dicts: list[APIReminderDirectMessageDict] = [
        reminder.to_reminder_dict() for reminder in reminders
    ]
    return reminder_dicts
