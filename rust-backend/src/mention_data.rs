pub(crate) const GROUP_MEMBERS_SQL: &str = "
    WITH RECURSIVE subgroups(id, root_id) AS (
        SELECT id, id FROM zulip.zerver_usergroup
        WHERE id = ANY(%s::integer[]) AND realm_id = %s
        UNION
        SELECT membership.subgroup_id, parent.root_id
        FROM zulip.zerver_groupgroupmembership AS membership
        JOIN subgroups AS parent ON parent.id = membership.supergroup_id
        JOIN zulip.zerver_namedusergroup AS named_group
          ON named_group.usergroup_ptr_id = membership.subgroup_id
    )
    SELECT subgroup.root_id, membership.user_profile_id
    FROM subgroups AS subgroup
    JOIN zulip.zerver_usergroupmembership AS membership
      ON membership.user_group_id = subgroup.id
    JOIN zulip.zerver_userprofile AS member ON member.id = membership.user_profile_id
    WHERE member.is_active
";

pub(crate) const PUBLIC_TOPIC_ANCHOR_SQL: &str = "
    SELECT id FROM zulip.zerver_message
    WHERE realm_id = %s AND recipient_id = %s AND is_channel_message
      AND upper(subject::text) = upper(%s)
    ORDER BY id DESC LIMIT 1
";

pub(crate) const PROTECTED_TOPIC_ANCHOR_SQL: &str = "
    SELECT message.id FROM zulip.zerver_usermessage AS receipt
    JOIN zulip.zerver_message AS message ON message.id = receipt.message_id
    WHERE receipt.user_profile_id = %s AND message.recipient_id = %s
      AND message.is_channel_message AND upper(message.subject::text) = upper(%s)
    ORDER BY receipt.id DESC LIMIT 1
";
