pub(crate) const RECURSIVE_GROUP_IDS_SQL: &str = "
    WITH RECURSIVE member_groups(id) AS (
        SELECT m.user_group_id FROM zulip.zerver_usergroupmembership AS m
        WHERE m.user_profile_id = %s
        UNION
        SELECT m.supergroup_id FROM zulip.zerver_groupgroupmembership AS m
        JOIN member_groups AS g ON g.id = m.subgroup_id
    )
    SELECT id FROM member_groups
";

pub(crate) const HAS_ACTIVE_MEMBER_SQL: &str = "
    WITH RECURSIVE subgroups(id) AS (
        SELECT id FROM zulip.zerver_usergroup WHERE id = %s
        UNION
        SELECT m.subgroup_id FROM zulip.zerver_groupgroupmembership AS m
        JOIN subgroups AS g ON g.id = m.supergroup_id
        JOIN zulip.zerver_namedusergroup AS n ON n.usergroup_ptr_id = m.subgroup_id
    )
    SELECT EXISTS (
        SELECT 1 FROM zulip.zerver_usergroupmembership AS m
        JOIN subgroups AS g ON g.id = m.user_group_id
        JOIN zulip.zerver_userprofile AS u ON u.id = m.user_profile_id
        WHERE u.id = ANY(%s::integer[]) AND u.is_active
    )
";

pub(crate) const CHANNEL_TOPIC_EXISTS_SQL: &str = "
    SELECT EXISTS (
        SELECT 1 FROM zulip.zerver_message
        WHERE realm_id = %s AND recipient_id = %s AND is_channel_message
          AND upper(subject::text) = upper(%s)
    )
";

pub(crate) const CONVERSATION_EXISTS_SQL: &str = "
    SELECT EXISTS (
        SELECT 1 FROM zulip.zerver_message WHERE realm_id = %s AND recipient_id = %s
    )
";
