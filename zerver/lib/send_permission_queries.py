from importlib import import_module

from django.db import connection

from zerver.lib.stream_subscription import num_subscribers_for_stream_id
from zerver.lib.topic import messages_for_topic
from zerver.lib.user_groups import get_recursive_membership_groups, is_any_user_in_group
from zerver.models import Message, Recipient, UserProfile


def channel_permission_group_ids(user: UserProfile, *, use_rust: bool) -> set[int]:
    if not use_rust:
        return set(get_recursive_membership_groups(user).values_list("id", flat=True))
    with connection.cursor() as cursor:
        return set(import_module("_zulip_backend").channel_permission_group_ids(cursor, user.id))


def dm_group_has_active_member(group_id: int, user_ids: set[int], *, use_rust: bool) -> bool:
    if not use_rust:
        return is_any_user_in_group(group_id, user_ids)
    with connection.cursor() as cursor:
        return import_module("_zulip_backend").dm_group_has_active_member(
            cursor, group_id, list(user_ids)
        )


def channel_topic_exists(
    realm_id: int, recipient_id: int, topic_name: str, *, use_rust: bool
) -> bool:
    if not use_rust:
        return messages_for_topic(realm_id, recipient_id, topic_name).exists()
    with connection.cursor() as cursor:
        return import_module("_zulip_backend").channel_topic_exists(
            cursor, realm_id, recipient_id, topic_name
        )


def channel_has_active_subscribers(channel_id: int, *, use_rust: bool) -> bool:
    if not use_rust:
        return num_subscribers_for_stream_id(channel_id) > 0
    with connection.cursor() as cursor:
        return import_module("_zulip_backend").channel_has_active_subscribers(
            cursor, channel_id, Recipient.STREAM
        )


def dm_conversation_exists(realm_id: int, recipient_id: int, *, use_rust: bool) -> bool:
    if not use_rust:
        return Message.objects.filter(realm_id=realm_id, recipient_id=recipient_id).exists()
    with connection.cursor() as cursor:
        return import_module("_zulip_backend").dm_conversation_exists(
            cursor, realm_id, recipient_id
        )
