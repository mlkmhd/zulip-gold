pub(crate) const USER_MESSAGE_EXISTS_SQL: &str = "
    SELECT EXISTS (
        SELECT 1 FROM zulip.zerver_usermessage
        WHERE user_profile_id = %s AND message_id = %s
    )
";

// A required message ID indicates that the channel hides pre-subscription history.
pub(crate) const RESTRICTED_CHANNEL_ACCESS_SQL: &str = "
    WITH RECURSIVE actor AS (
        SELECT %s::integer AS id, %s::boolean AS is_guest, %s::bigint AS required_message_id
    ), member_groups(id) AS (
        SELECT m.user_group_id FROM zulip.zerver_usergroupmembership AS m
        JOIN actor ON actor.id = m.user_profile_id
        WHERE NOT actor.is_guest
        UNION
        SELECT m.supergroup_id FROM zulip.zerver_groupgroupmembership AS m
        JOIN member_groups AS g ON g.id = m.subgroup_id
    )
    SELECT (actor.required_message_id IS NULL OR EXISTS (
        SELECT 1 FROM zulip.zerver_usermessage AS um
        WHERE um.user_profile_id = actor.id AND um.message_id = actor.required_message_id
    )) AND (
        EXISTS (
            SELECT 1 FROM zulip.zerver_subscription AS sub
            WHERE sub.user_profile_id = actor.id AND sub.recipient_id = %s AND sub.active
        ) OR EXISTS (
            SELECT 1 FROM member_groups AS g WHERE g.id IN (%s, %s)
        )
    )
    FROM actor
";
