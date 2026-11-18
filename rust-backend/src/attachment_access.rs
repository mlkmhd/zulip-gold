pub(crate) const REFRESH_REALM_PUBLIC_SQL: &str = "
    UPDATE zulip.zerver_attachment AS attachment
    SET is_realm_public = EXISTS (
        SELECT 1 FROM zulip.zerver_attachment_messages AS link
        JOIN zulip.zerver_message AS message ON message.id = link.message_id
        JOIN zulip.zerver_stream AS channel ON channel.recipient_id = message.recipient_id
        WHERE link.attachment_id = attachment.id AND message.realm_id = %s
          AND NOT channel.invite_only
    )
    WHERE attachment.id = %s AND attachment.is_realm_public IS NULL
";

pub(crate) const MESSAGE_ACCESS_SQL: &str = "
    WITH RECURSIVE actor AS (
        SELECT %s::integer AS attachment_id, %s::integer AS id,
               %s::boolean AS is_guest, %s::smallint AS channel_type
    ), linked_messages AS (
        SELECT message.id, message.is_channel_message, message.recipient_id,
               recipient.type_id AS channel_id
        FROM zulip.zerver_attachment_messages AS link
        JOIN zulip.zerver_message AS message ON message.id = link.message_id
        JOIN zulip.zerver_recipient AS recipient ON recipient.id = message.recipient_id
        JOIN actor ON actor.attachment_id = link.attachment_id
    ), received_channels AS (
        SELECT message.channel_id FROM linked_messages AS message
        JOIN zulip.zerver_usermessage AS receipt ON receipt.message_id = message.id
        JOIN actor ON actor.id = receipt.user_profile_id
        WHERE message.is_channel_message
    ), subscribed_channels AS (
        SELECT recipient.type_id AS id FROM zulip.zerver_subscription AS subscription
        JOIN actor ON actor.id = subscription.user_profile_id
        JOIN zulip.zerver_recipient AS recipient ON recipient.id = subscription.recipient_id
        WHERE subscription.active AND recipient.type = actor.channel_type
          AND recipient.id IN (SELECT recipient_id FROM linked_messages)
    ), member_groups(id) AS (
        SELECT membership.user_group_id FROM zulip.zerver_usergroupmembership AS membership
        JOIN actor ON actor.id = membership.user_profile_id
        WHERE NOT actor.is_guest
        UNION
        SELECT membership.supergroup_id FROM zulip.zerver_groupgroupmembership AS membership
        JOIN member_groups AS subgroup ON subgroup.id = membership.subgroup_id
    )
    SELECT EXISTS (
        SELECT 1 FROM linked_messages AS message
        JOIN zulip.zerver_usermessage AS receipt ON receipt.message_id = message.id
        JOIN actor ON actor.id = receipt.user_profile_id
        WHERE NOT message.is_channel_message
    ) OR EXISTS (
        SELECT 1 FROM received_channels AS received
        JOIN subscribed_channels AS subscribed ON subscribed.id = received.channel_id
    ) OR EXISTS (
        SELECT 1 FROM zulip.zerver_stream AS channel
        WHERE channel.id IN (
            SELECT channel_id FROM linked_messages WHERE is_channel_message
        ) AND (
            (channel.history_public_to_subscribers
             AND channel.id IN (SELECT id FROM subscribed_channels))
            OR (
                EXISTS (
                    SELECT 1 FROM member_groups AS permission_group
                    WHERE permission_group.id IN (
                        channel.can_subscribe_group_id, channel.can_add_subscribers_group_id
                    )
                ) AND (
                    channel.history_public_to_subscribers
                    OR channel.id IN (SELECT channel_id FROM received_channels)
                )
            )
        )
    )
";
