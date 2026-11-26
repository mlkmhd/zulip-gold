import os
import sys
from enum import Enum
from importlib import import_module
from typing import cast

from django.conf import settings
from django.utils.translation import gettext as _
from django_stubs_ext import StrPromise

from zerver.lib.compose_reply import (
    CHANNEL_MESSAGE_QUOTE_CONTEXT,
    DIRECT_MESSAGE_QUOTE_CONTEXT,
    MESSAGE_QUOTE_BODY,
)
from zerver.lib.display_recipient import get_display_recipient
from zerver.lib.event_types import RemindersRemoveEvent
from zerver.lib.exceptions import JsonableError, ResourceNotFoundError
from zerver.lib.markdown.fenced_code import get_unused_fence
from zerver.lib.mention import silent_mention_syntax_for_user
from zerver.lib.message import access_message, format_sorted_user_mentions, truncate_content
from zerver.lib.message_cache import MessageDict
from zerver.lib.native_backend import load_native_contract
from zerver.lib.topic_link_util import (
    TOPIC_LINK_SYNTAX_FOR_DISPLAY,
    escape_invalid_stream_topic_characters,
)
from zerver.lib.types import UserDisplayRecipient
from zerver.lib.url_encoding import message_link_url, stream_message_url
from zerver.models import Message, Stream, UserProfile
from zerver.models.scheduled_jobs import ScheduledMessage
from zerver.tornado.django_api import send_event_on_commit

_rust_reminder_note_text = load_native_contract(
    "ZULIP_RUST_REMINDER_NOTE_TEXT", "REMINDER_NOTE_TEXT_API"
)
_rust_exact_reminder_quote = load_native_contract(
    "ZULIP_RUST_REMINDER_QUOTE_TEXT", "REMINDER_QUOTE_TEXT_API"
)
_reference_quote_body = MESSAGE_QUOTE_BODY
_reference_unused_fence = get_unused_fence
_reference_truncate_content = truncate_content

_rust_reminder_quote = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_REMINDER_QUOTE") == "1" else None
)
_rust_reminder_context = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_REMINDER_CONTEXT") == "1"
    else None
)


USE_RUST_FOR_REMINDER_ACCESS = os.environ.get("ZULIP_RUST_REMINDER_ACCESS") == "1"


def access_reminder_target(user: UserProfile, message_id: int) -> Message:
    return access_message(
        user,
        message_id,
        is_modifying_message=False,
        use_rust_for_read_access=USE_RUST_FOR_REMINDER_ACCESS,
    )


def normalize_note_text(body: str) -> str:
    max_length = settings.MAX_REMINDER_NOTE_LENGTH
    rust_result: tuple[bool, str] | None = None
    normalize = getattr(_rust_reminder_note_text, "normalize_exact_reminder_note", None)
    if normalize is not None and type(body) is str:
        rust_result = cast("tuple[bool, str] | None", normalize(body, max_length))
    if (
        rust_result is None
        and _rust_reminder_context is not None
        and 0 <= max_length <= sys.maxsize
    ):
        rust_result = _rust_reminder_context.normalize_reminder_note(body, max_length)

    if rust_result is None:
        # Similar to zerver.lib.message.normalize_body
        body = body.rstrip().lstrip("\n")
        valid = len(body) <= max_length
    else:
        valid, body = rust_result
    if not valid:
        raise JsonableError(
            _("Maximum reminder note length: {max_length} characters").format(max_length=max_length)
        )

    return body


class ReminderRecipientType(Enum):
    CHANNEL = "channel"
    PRIVATE = "private"
    NOTE_TO_SELF = "note to self"


def get_reminder_formatted_content(
    message: Message, current_user: UserProfile, note: str | None = None
) -> str:
    if note:
        note = normalize_note_text(note)

    format_recipient_type_key: ReminderRecipientType
    user_silent_mention = silent_mention_syntax_for_user(message.sender)
    conversation_url = message_link_url(current_user.realm, MessageDict.wide_dict(message))

    if message.is_channel_message:
        # We don't need to check access here since we already have the message
        # whose access has already been checked by the caller.
        stream = Stream.objects.get(
            id=message.recipient.type_id,
            realm=current_user.realm,
        )
        url = stream_message_url(
            realm=None,
            message={
                "id": message.id,
                "stream_id": stream.id,
                "display_recipient": stream.name,
                "topic": message.topic_name(),
            },
            conversation_link=True,
        )
        topic_pretty_link: str | None = None
        if _rust_reminder_context is not None:
            topic_pretty_link = _rust_reminder_context.reminder_channel_topic_link(
                stream.name, message.topic_name(), url
            )
        if topic_pretty_link is None:
            escape = escape_invalid_stream_topic_characters
            label = TOPIC_LINK_SYNTAX_FOR_DISPLAY.format(
                channel_name=escape(stream.name),
                topic_name=escape(message.topic_name()),
            )
            topic_pretty_link = f"[{label}]({url})"
        if note:
            content = _(
                "You requested a reminder for the following message. Note:\n > {note}"
            ).format(
                note=note,
            )
        else:
            content = _("You requested a reminder for the following message.")

        format_recipient_type_key = ReminderRecipientType.CHANNEL
        context = dict(
            user_silent_mention=user_silent_mention,
            conversation_url=conversation_url,
            topic_pretty_link=topic_pretty_link,
        )
    else:
        if note:
            content = _(
                "You requested a reminder for the following direct message. Note:\n > {note}"
            ).format(
                note=note,
            )
        else:
            content = _("You requested a reminder for the following direct message.")
        recipients: list[UserDisplayRecipient] = get_display_recipient(message.recipient)
        mentions: list[str] | None = None
        if _rust_reminder_context is not None:
            mentions = _rust_reminder_context.reminder_direct_recipient_mentions(
                message.sender_id, [(user["id"], user["full_name"]) for user in recipients]
            )
        if mentions is None:
            mentions = sorted(
                silent_mention_syntax_for_user(user)
                for user in recipients
                if user["id"] != message.sender_id
            )

        if not mentions:
            format_recipient_type_key = ReminderRecipientType.NOTE_TO_SELF
            context = dict(
                conversation_url=conversation_url,
            )
        else:
            format_recipient_type_key = ReminderRecipientType.PRIVATE
            context = dict(
                user_silent_mention=user_silent_mention,
                conversation_url=conversation_url,
                list_of_recipient_mentions=format_sorted_user_mentions(mentions),
            )

    # Format the message content as a quote.
    content += "\n\n"

    REMINDER_FORMAT: dict[ReminderRecipientType, dict[str, str | StrPromise]] = {
        ReminderRecipientType.CHANNEL: {
            "widget": _(
                "{user_silent_mention} [sent]({conversation_url}) a {widget} in {topic_pretty_link}."
            ),
            "text": CHANNEL_MESSAGE_QUOTE_CONTEXT,
        },
        ReminderRecipientType.PRIVATE: {
            "widget": _(
                "{user_silent_mention} [sent]({conversation_url}) a {widget} to {list_of_recipient_mentions}."
            ),
            "text": DIRECT_MESSAGE_QUOTE_CONTEXT,
        },
        ReminderRecipientType.NOTE_TO_SELF: {
            "widget": _("You [sent]({conversation_url}) yourself a {widget}."),
            "text": _("You [sent]({conversation_url}) a note to yourself:"),
        },
    }

    widget: str | None = None
    if _rust_reminder_context is not None:
        widget = _rust_reminder_context.reminder_widget_label(message.content[:5])
    elif message.content.startswith("/poll"):
        widget = "poll"
    elif message.content.startswith("/todo"):
        widget = "todo list"

    if widget is not None:
        context.update(widget=widget)
        content += REMINDER_FORMAT[format_recipient_type_key]["widget"].format_map(context)
    else:
        content += REMINDER_FORMAT[format_recipient_type_key]["text"].format_map(context)
        content += "\n"
        append_exact = getattr(_rust_exact_reminder_quote, "append_exact_reminder_quote", None)
        if (
            append_exact is not None
            and MESSAGE_QUOTE_BODY is _reference_quote_body
            and get_unused_fence is _reference_unused_fence
            and truncate_content is _reference_truncate_content
        ):
            result = append_exact(content, message.content, settings.MAX_MESSAGE_LENGTH)
            if result is not None:
                return cast(str, result)
        if _rust_reminder_quote is not None and 0 <= settings.MAX_MESSAGE_LENGTH <= sys.maxsize:
            quoted: str | None = _rust_reminder_quote.append_reminder_quote(
                content, message.content, settings.MAX_MESSAGE_LENGTH
            )
            if quoted is not None:
                return quoted
        fence = get_unused_fence(message.content)
        length_without_message_content = len(
            content + MESSAGE_QUOTE_BODY.format(fence=fence, content="")
        )
        max_length = settings.MAX_MESSAGE_LENGTH - length_without_message_content
        msg_content = truncate_content(message.content, max_length, "\n[message truncated]")
        content += MESSAGE_QUOTE_BODY.format(
            fence=fence,
            content=msg_content,
        )
    return content


def access_reminder(user_profile: UserProfile, reminder_id: int) -> ScheduledMessage:
    try:
        return ScheduledMessage.objects.get(
            id=reminder_id, sender=user_profile, delivery_type=ScheduledMessage.REMIND
        )
    except ScheduledMessage.DoesNotExist:
        raise ResourceNotFoundError(_("Reminder does not exist"))


def notify_remove_reminder(user_profile: UserProfile, reminder_id: int) -> None:
    event = RemindersRemoveEvent(reminder_id=reminder_id)
    send_event_on_commit(user_profile.realm, event, [user_profile.id])
