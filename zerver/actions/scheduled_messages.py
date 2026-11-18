import logging
import os
from collections.abc import Sequence
from datetime import datetime, timedelta
from importlib import import_module
from typing import cast

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from django.utils.translation import override as override_language

from zerver.actions.message_send import (
    check_message,
    do_send_messages,
    internal_send_private_message,
)
from zerver.actions.uploads import check_attachment_reference_change, do_claim_attachments
from zerver.lib.addressee import Addressee
from zerver.lib.display_recipient import get_recipient_ids
from zerver.lib.event_types import (
    ReminderFields,
    RemindersAddEvent,
    ScheduledMessageFields,
    ScheduledMessagesAddEvent,
    ScheduledMessagesRemoveEvent,
    ScheduledMessagesUpdateEvent,
)
from zerver.lib.exceptions import (
    DeliveryTimeNotInFutureError,
    JsonableError,
    RealmDeactivatedError,
    UserDeactivatedError,
)
from zerver.lib.message import SendMessageRequest, access_message, truncate_topic
from zerver.lib.reminders import get_reminder_formatted_content, notify_remove_reminder
from zerver.lib.scheduled_messages import (
    access_scheduled_message,
    parse_scheduled_message_recipient,
)
from zerver.lib.string_validation import check_stream_topic
from zerver.lib.timestamp import datetime_to_global_time
from zerver.models import Client, Realm, ScheduledMessage, Subscription, UserProfile
from zerver.models.users import get_system_bot, is_cross_realm_bot_email
from zerver.tornado.django_api import send_event_on_commit

SCHEDULED_MESSAGE_LATE_CUTOFF_MINUTES = 10

_rust_scheduled_job_creation = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_SCHEDULED_JOB_CREATION") == "1"
    else None
)
_rust_scheduled_message_edit = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_SCHEDULED_MESSAGE_EDIT") == "1"
    else None
)
_rust_scheduled_job_claim = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_SCHEDULED_JOB_CLAIM") == "1"
    else None
)
_rust_scheduled_job_result = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_SCHEDULED_JOB_RESULT") == "1"
    else None
)


def check_schedule_message(
    sender: UserProfile,
    client: Client,
    recipient_type_name: str,
    message_to: list[int],
    topic_name: str | None,
    message_content: str,
    deliver_at: datetime,
    realm: Realm | None = None,
    *,
    read_by_sender: bool | None = None,
    skip_events: bool = False,
) -> int:
    addressee = Addressee.legacy_build(sender, recipient_type_name, message_to, topic_name, realm)
    send_request = check_message(
        sender,
        client,
        addressee,
        message_content,
        realm=realm,
    )
    send_request.deliver_at = deliver_at

    if read_by_sender is None:
        # Legacy default: a scheduled message you sent from a non-API client is
        # automatically marked as read for yourself, unless it was sent to
        # yourself only.
        read_by_sender = client.default_read_by_sender() and not addressee.is_message_to_self(
            sender
        )

    return do_schedule_messages(
        [send_request],
        sender,
        read_by_sender=read_by_sender,
        skip_events=skip_events,
        delivery_type=ScheduledMessage.SEND_LATER,
    )[0]


def notify_new_scheduled_message(
    user_profile: UserProfile, scheduled_messages: list[ScheduledMessage]
) -> None:
    event = ScheduledMessagesAddEvent(
        scheduled_messages=[
            ScheduledMessageFields(**scheduled_message.to_dict())
            for scheduled_message in scheduled_messages
        ],
    )
    send_event_on_commit(user_profile.realm, event, [user_profile.id])


def notify_new_reminder(user_profile: UserProfile, reminders: list[ScheduledMessage]) -> None:
    event = RemindersAddEvent(
        reminders=[ReminderFields(**reminder.to_reminder_dict()) for reminder in reminders],
    )
    send_event_on_commit(user_profile.realm, event, [user_profile.id])


def insert_scheduled_messages(messages: list[ScheduledMessage]) -> None:
    if _rust_scheduled_job_creation is None:
        ScheduledMessage.objects.bulk_create(messages)
        return
    if not messages:
        return

    with connection.cursor() as cursor:
        rows = cast(
            list[tuple[int]],
            _rust_scheduled_job_creation.insert_scheduled_jobs(
                cursor,
                {
                    "sender_id": [message.sender_id for message in messages],
                    "recipient_id": [message.recipient_id for message in messages],
                    "subject": [message.subject for message in messages],
                    "content": [message.content for message in messages],
                    "rendered_content": [message.rendered_content for message in messages],
                    "sending_client_id": [message.sending_client_id for message in messages],
                    "stream_id": [message.stream_id for message in messages],
                    "realm_id": [message.realm_id for message in messages],
                    "scheduled_timestamp": [message.scheduled_timestamp for message in messages],
                    "read_by_sender": [message.read_by_sender for message in messages],
                    "request_timestamp": [message.request_timestamp for message in messages],
                    "delivery_type": [message.delivery_type for message in messages],
                    "reminder_target_message_id": [
                        message.reminder_target_message_id for message in messages
                    ],
                    "reminder_note": [message.reminder_note for message in messages],
                },
            ),
        )
    for message, (message_id,) in zip(messages, rows, strict=True):
        message.id = message_id
        message._state.adding = False
        message._state.db = connection.alias


def do_schedule_messages(
    send_message_requests: Sequence[SendMessageRequest],
    sender: UserProfile,
    *,
    read_by_sender: bool = False,
    skip_events: bool = False,
    delivery_type: int,
) -> list[int]:
    scheduled_messages: list[tuple[ScheduledMessage, SendMessageRequest]] = []

    for send_request in send_message_requests:
        scheduled_message = ScheduledMessage()
        scheduled_message.sender = send_request.message.sender
        scheduled_message.recipient = send_request.message.recipient
        topic_name = send_request.message.topic_name()
        scheduled_message.set_topic_name(topic_name=topic_name)
        scheduled_message.content = send_request.message.content
        scheduled_message.rendered_content = send_request.rendering_result.rendered_content
        scheduled_message.sending_client = send_request.message.sending_client
        scheduled_message.stream = send_request.stream
        scheduled_message.realm = send_request.realm
        assert send_request.deliver_at is not None
        scheduled_message.scheduled_timestamp = send_request.deliver_at
        scheduled_message.read_by_sender = read_by_sender
        scheduled_message.delivery_type = delivery_type

        if delivery_type == ScheduledMessage.REMIND:
            scheduled_message.reminder_target_message_id = send_request.reminder_target_message_id
            scheduled_message.reminder_note = send_request.reminder_note

        scheduled_messages.append((scheduled_message, send_request))

    with transaction.atomic(durable=True):
        scheduled_message_objects = [
            scheduled_message for scheduled_message, ignored in scheduled_messages
        ]
        insert_scheduled_messages(scheduled_message_objects)
        for scheduled_message, send_request in scheduled_messages:
            if do_claim_attachments(
                scheduled_message, send_request.rendering_result.potential_attachment_path_ids
            ):
                scheduled_message.has_attachment = True
                scheduled_message.save(update_fields=["has_attachment"])

        if not skip_events:
            if delivery_type == ScheduledMessage.REMIND:
                notify_new_reminder(sender, scheduled_message_objects)
            else:
                notify_new_scheduled_message(sender, scheduled_message_objects)
    return [scheduled_message.id for scheduled_message, ignored in scheduled_messages]


def notify_update_scheduled_message(
    user_profile: UserProfile, scheduled_message: ScheduledMessage
) -> None:
    event = ScheduledMessagesUpdateEvent(
        scheduled_message=ScheduledMessageFields(**scheduled_message.to_dict()),
    )
    send_event_on_commit(user_profile.realm, event, [user_profile.id])


@transaction.atomic(durable=True)
def edit_scheduled_message(
    sender: UserProfile,
    client: Client,
    scheduled_message_id: int,
    recipient_type_name: str | None,
    message_to: int | list[int] | None,
    topic_name: str | None,
    message_content: str | None,
    deliver_at: datetime | None,
    realm: Realm,
) -> None:
    scheduled_message_object = access_scheduled_message(
        sender, scheduled_message_id, lock_message=True
    )

    # We locked the row above, so a concurrent delivery has already
    # committed and this guard reliably observes its delivered=True.
    if scheduled_message_object.delivered is True:
        raise JsonableError(_("Scheduled message was already sent"))

    # If the server failed to send the scheduled message, a new scheduled
    # delivery timestamp (`deliver_at`) is required.
    if scheduled_message_object.failed and deliver_at is None:
        raise DeliveryTimeNotInFutureError

    # Get existing scheduled message's recipient IDs and recipient_type_name.
    existing_recipient, existing_recipient_type_name = get_recipient_ids(
        scheduled_message_object.recipient, sender.id
    )

    send_request: SendMessageRequest | None = None
    # If any recipient information or message content has been updated,
    # we check the message again.
    if recipient_type_name is not None or message_to is not None or message_content is not None:
        # Update message type if changed.
        if recipient_type_name is not None:
            updated_recipient_type_name = recipient_type_name
        else:
            updated_recipient_type_name = existing_recipient_type_name

        # Update message recipient if changed.
        if message_to is not None:
            updated_recipient_type_name, updated_recipient = parse_scheduled_message_recipient(
                updated_recipient_type_name, message_to
            )
        else:
            updated_recipient = existing_recipient

        # Update topic name if changed.
        if topic_name is not None:
            updated_topic_name = topic_name
        else:
            # This will be ignored in Addressee.legacy_build if type
            # is being changed from stream to direct.
            updated_topic_name = scheduled_message_object.topic_name()

        # Update message content if changed.
        if message_content is not None:
            updated_content = message_content
        else:
            updated_content = scheduled_message_object.content

        # Check message again.
        addressee = Addressee.legacy_build(
            sender, updated_recipient_type_name, updated_recipient, updated_topic_name
        )
        send_request = check_message(
            sender,
            client,
            addressee,
            updated_content,
            realm=realm,
        )

    if recipient_type_name is not None or message_to is not None:
        assert send_request is not None
        # User has updated the scheduled message's recipient.
        scheduled_message_object.recipient = send_request.message.recipient
        scheduled_message_object.stream = send_request.stream
        # Update the topic based on the new recipient information.
        new_topic_name = send_request.message.topic_name()
        scheduled_message_object.set_topic_name(topic_name=new_topic_name)
    elif topic_name is not None and existing_recipient_type_name == "stream":
        # User has updated the scheduled message's topic, but not
        # the existing recipient information. We ignore topics sent
        # for scheduled direct messages.
        check_stream_topic(topic_name)
        new_topic_name = truncate_topic(topic_name)
        scheduled_message_object.set_topic_name(topic_name=new_topic_name)

    if message_content is not None:
        assert send_request is not None
        # User has updated the scheduled messages's content.
        scheduled_message_object.content = send_request.message.content
        scheduled_message_object.rendered_content = send_request.rendering_result.rendered_content
        attachment_reference_change = check_attachment_reference_change(
            scheduled_message_object, send_request.rendering_result
        )
        scheduled_message_object.has_attachment = attachment_reference_change.did_attachment_change

    if deliver_at is not None:
        # User has updated the scheduled message's send timestamp.
        scheduled_message_object.scheduled_timestamp = deliver_at

    # Update for most recent Client information.
    scheduled_message_object.sending_client = client

    # If the user is editing a scheduled message that the server tried
    # and failed to send, we need to update the `failed` boolean field
    # as well as the associated `failure_message` field.
    if scheduled_message_object.failed:
        scheduled_message_object.failed = False
        scheduled_message_object.failure_message = None

    if _rust_scheduled_message_edit is None:
        scheduled_message_object.save()
    else:
        with connection.cursor() as cursor:
            updated = _rust_scheduled_message_edit.update_scheduled_message(
                cursor,
                {
                    "id": scheduled_message_object.id,
                    "sender_id": sender.id,
                    "delivery_type": ScheduledMessage.SEND_LATER,
                    "recipient_id": scheduled_message_object.recipient_id,
                    "stream_id": scheduled_message_object.stream_id,
                    "subject": scheduled_message_object.subject,
                    "content": scheduled_message_object.content,
                    "rendered_content": scheduled_message_object.rendered_content,
                    "has_attachment": scheduled_message_object.has_attachment,
                    "scheduled_timestamp": scheduled_message_object.scheduled_timestamp,
                    "sending_client_id": scheduled_message_object.sending_client_id,
                    "failed": scheduled_message_object.failed,
                    "failure_message": scheduled_message_object.failure_message,
                },
            )
        if not updated:
            raise DatabaseError("Scheduled message update did not affect any rows.")

    notify_update_scheduled_message(sender, scheduled_message_object)


def notify_remove_scheduled_message(user_profile: UserProfile, scheduled_message_id: int) -> None:
    event = ScheduledMessagesRemoveEvent(scheduled_message_id=scheduled_message_id)
    send_event_on_commit(user_profile.realm, event, [user_profile.id])


@transaction.atomic(durable=True)
def delete_scheduled_message(user_profile: UserProfile, scheduled_message_id: int) -> None:
    scheduled_message_object = access_scheduled_message(user_profile, scheduled_message_id)
    scheduled_message_id = scheduled_message_object.id
    scheduled_message_object.delete()
    notify_remove_scheduled_message(user_profile, scheduled_message_id)


def mark_scheduled_job_delivered(scheduled_message: ScheduledMessage, message_id: int) -> None:
    scheduled_message.delivered_message_id = message_id
    scheduled_message.delivered = True
    if _rust_scheduled_job_result is None:
        scheduled_message.save(update_fields=["delivered", "delivered_message_id"])
    else:
        with connection.cursor() as cursor:
            updated = _rust_scheduled_job_result.mark_scheduled_job_delivered(
                cursor, scheduled_message.id, message_id
            )
        if not updated:
            raise DatabaseError("Scheduled job delivery update did not affect any rows.")


def send_reminder(scheduled_message: ScheduledMessage) -> None:
    message_id = scheduled_message.reminder_target_message_id
    assert message_id is not None
    current_user = scheduled_message.sender
    try:
        message = access_message(current_user, message_id, is_modifying_message=False)
        content = get_reminder_formatted_content(
            message, current_user, scheduled_message.reminder_note
        )
    except JsonableError:
        # If we no longer have access to the message, we send the reminder with the
        # last known message position and content.
        content = scheduled_message.content
    # Reminder messages are always sent from the notification bot. We use acting_user
    # to have appropriate permissions for the messages.
    message_id = internal_send_private_message(
        get_system_bot(settings.NOTIFICATION_BOT, scheduled_message.realm.id),
        current_user,
        content,
        acting_user=current_user,
    )
    if message_id is None:
        # internal_send_private_message swallows JsonableError from
        # check_message and returns None; surface that as a failure so
        # the worker marks the row failed rather than delivered with a
        # NULL delivered_message_id.
        raise JsonableError(_("Reminder could not be sent."))
    mark_scheduled_job_delivered(scheduled_message, message_id)
    notify_remove_reminder(current_user, scheduled_message.id)


def send_scheduled_message(scheduled_message: ScheduledMessage) -> None:
    assert not scheduled_message.delivered
    assert not scheduled_message.failed

    # Repeat the checks from validate_account_and_subdomain, in case
    # the state changed since the message was scheduled.
    if scheduled_message.realm.deactivated:
        raise RealmDeactivatedError

    if not scheduled_message.sender.is_active:
        raise UserDeactivatedError

    # Reminders go to the sender's own DMs from Notification Bot, so
    # the late-cutoff concern (stale messages surprising other
    # recipients) doesn't apply. Dispatch before the cutoff check so
    # reminders still fire if the worker is backed up.
    if scheduled_message.delivery_type == ScheduledMessage.REMIND:
        send_reminder(scheduled_message)
        return

    # Limit how late we're willing to send a scheduled message.
    latest_send_time = scheduled_message.scheduled_timestamp + timedelta(
        minutes=SCHEDULED_MESSAGE_LATE_CUTOFF_MINUTES
    )
    if timezone_now() > latest_send_time:
        raise JsonableError(_("Message could not be sent at the scheduled time."))

    # Recheck that we have permission to send this message, in case
    # permissions have changed since the message was scheduled.
    if scheduled_message.stream is not None:
        addressee = Addressee.for_stream(scheduled_message.stream, scheduled_message.topic_name())
    else:
        subscriber_ids = list(
            Subscription.objects.filter(recipient=scheduled_message.recipient).values_list(
                "user_profile_id", flat=True
            )
        )
        addressee = Addressee.for_user_ids(subscriber_ids, scheduled_message.realm)

    # Calling check_message again is important because permissions may
    # have changed since the message was originally scheduled. This
    # means that Markdown syntax referencing mutable organization data
    # (for example, mentioning a user by name) will work (or not) as
    # if the message was sent at the delivery time, not the sending
    # time.
    send_request = check_message(
        scheduled_message.sender,
        scheduled_message.sending_client,
        addressee,
        scheduled_message.content,
        scheduled_message.realm,
    )

    sent_message_result = do_send_messages(
        [send_request],
        mark_as_read=[scheduled_message.sender_id] if scheduled_message.read_by_sender else [],
    )[0]
    mark_scheduled_job_delivered(scheduled_message, sent_message_result.message_id)
    notify_remove_scheduled_message(scheduled_message.sender, scheduled_message.id)


def send_failed_scheduled_message_notification(
    user_profile: UserProfile, scheduled_message_id: int
) -> None:
    scheduled_message = access_scheduled_message(user_profile, scheduled_message_id)

    with override_language(user_profile.default_language):
        error_string = scheduled_message.failure_message
        delivery_time_markdown = datetime_to_global_time(scheduled_message.scheduled_timestamp)

        content = "".join(
            [
                _(
                    "The message you scheduled for {delivery_datetime} was not sent because of the following error:"
                ),
                "\n\n",
                "> {error_message}",
                "\n\n",
                _("[View scheduled messages](#scheduled)"),
                "\n\n",
            ]
        )

    content = content.format(
        delivery_datetime=delivery_time_markdown,
        error_message=error_string,
    )

    internal_send_private_message(
        get_system_bot(settings.NOTIFICATION_BOT, user_profile.realm_id),
        user_profile,
        content,
    )


@transaction.atomic(durable=True)
def try_deliver_one_scheduled_message() -> bool:
    # Returns whether there was a scheduled message to attempt
    # delivery on, regardless of whether delivery succeeded.
    if _rust_scheduled_job_claim is None:
        scheduled_message = (
            ScheduledMessage.objects.filter(
                scheduled_timestamp__lte=timezone_now(),
                delivered=False,
                failed=False,
            )
            .select_for_update(no_key=True)
            .first()
        )
    else:
        scheduled_message = cast(
            ScheduledMessage | None,
            _rust_scheduled_job_claim.lock_due_scheduled_job(
                ScheduledMessage.objects.using(connection.alias), timezone_now()
            ),
        )

    if scheduled_message is None:
        return False

    logging.info(
        "Sending scheduled message %s with date %s (sender: %s)",
        scheduled_message.id,
        scheduled_message.scheduled_timestamp,
        scheduled_message.sender_id,
    )

    with override_language(scheduled_message.sender.default_language):
        try:
            send_scheduled_message(scheduled_message)
        except Exception as e:
            scheduled_message.refresh_from_db()
            was_delivered = scheduled_message.delivered
            scheduled_message.failed = True

            if isinstance(e, JsonableError):
                scheduled_message.failure_message = e.msg
                logging.info("Failed with message: %s", e.msg)
            else:
                # An unexpected failure; store and send user a generic
                # internal server error in notification message.
                scheduled_message.failure_message = _("Internal server error")
                logging.exception(
                    "Unexpected error sending scheduled message %s (sent: %s)",
                    scheduled_message.id,
                    was_delivered,
                    stack_info=True,
                )

            if _rust_scheduled_job_result is None:
                scheduled_message.save(update_fields=["failed", "failure_message"])
            else:
                with connection.cursor() as cursor:
                    updated = _rust_scheduled_job_result.mark_scheduled_job_failed(
                        cursor, scheduled_message.id, scheduled_message.failure_message
                    )
                if not updated:
                    raise DatabaseError("Scheduled job failure update did not affect any rows.")

            if (
                not was_delivered
                # Reminders have their own notification system.
                and scheduled_message.delivery_type != ScheduledMessage.REMIND
                # Do not send notification if either the realm or
                # the sending user account has been deactivated.
                and not isinstance(e, RealmDeactivatedError)
                and not isinstance(e, UserDeactivatedError)
                # Cross-realm system bots (welcome-bot, notification-bot,
                # emailgateway) have no human behind them to read the
                # notification, and notifying them leaves a stray
                # notification-bot <-> system-bot DirectMessageGroup
                # attached to no realm.
                and not is_cross_realm_bot_email(scheduled_message.sender.delivery_email)
            ):
                notify_update_scheduled_message(scheduled_message.sender, scheduled_message)
                send_failed_scheduled_message_notification(
                    scheduled_message.sender, scheduled_message.id
                )

    return True
