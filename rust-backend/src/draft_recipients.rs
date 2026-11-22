use std::collections::{HashMap, HashSet};

// These paths both require content access, not just channel metadata.
macro_rules! channel_content_query {
    ($actor:literal, $select:literal, $active_limit:literal, $is_guest:literal) => {
        concat!(
            "
    WITH RECURSIVE actor AS (",
            $actor,
            "), member_groups(id) AS (
        SELECT m.user_group_id FROM zulip.zerver_usergroupmembership AS m
        JOIN actor ON actor.id = m.user_profile_id
        UNION
        SELECT m.supergroup_id FROM zulip.zerver_groupgroupmembership AS m
        JOIN member_groups AS g ON g.id = m.subgroup_id
    )
    ",
            $select,
            $active_limit,
            "
      AND CASE
          WHEN s.is_web_public THEN TRUE
          WHEN EXISTS (
              SELECT 1 FROM zulip.zerver_subscription AS sub
              WHERE sub.recipient_id = s.recipient_id
                AND sub.user_profile_id = actor.id AND sub.active
          ) THEN TRUE
          WHEN ",
            $is_guest,
            " THEN FALSE
          WHEN NOT s.invite_only THEN TRUE
          ELSE EXISTS (
              SELECT 1 FROM member_groups AS g
              WHERE g.id IN (s.can_subscribe_group_id, s.can_add_subscribers_group_id)
          )
      END
"
        )
    };
}

pub(crate) const CHANNEL_RECIPIENT_SQL: &str = channel_content_query!(
    "SELECT %s::integer AS id",
    "SELECT s.recipient_id FROM zulip.zerver_stream AS s CROSS JOIN actor
     WHERE s.id = %s::bigint AND s.realm_id = %s::integer",
    "
      AND NOT s.deactivated",
    "%s::boolean"
);

// A scalar name lookup retains Django's one-result requirement. An inactive
// account/realm principal cannot reach the existing independent content gate.
#[cfg(feature = "http")]
pub(crate) const CHANNEL_ID_BY_NAME_SQL: &str = channel_content_query!(
    "SELECT id, role FROM zulip.zerver_userprofile
     WHERE id = $1 AND realm_id = $2 AND is_active",
    "SELECT s.id::bigint FROM zulip.zerver_stream AS s CROSS JOIN actor
     WHERE s.realm_id = $2 AND s.id = (
         SELECT lookup.id FROM zulip.zerver_stream AS lookup
          WHERE lookup.realm_id = $2 AND UPPER(lookup.name::text) = UPPER($3::text)
     )",
    "
      AND NOT s.deactivated",
    "actor.role = 600"
);

// Archived channels are included here, matching topic history's content gate.
// The lateral join leaves one empty row for a visible channel with no messages.
#[cfg(feature = "http")]
pub(crate) const CHANNEL_TOPIC_HISTORY_SQL: &str = concat!(
    "SELECT history.subject, history.max_id FROM (",
    channel_content_query!(
        "SELECT id, role FROM zulip.zerver_userprofile
         WHERE id = $1 AND realm_id = $2 AND is_active",
        "SELECT s.recipient_id, s.history_public_to_subscribers AS public_history
         FROM zulip.zerver_stream AS s CROSS JOIN actor
         WHERE s.realm_id = $2 AND s.id = $3::bigint",
        "",
        "actor.role = 600"
    ),
    ") AS visible
     LEFT JOIN LATERAL (
         SELECT message.subject, max(message.id)::bigint AS max_id
         FROM zulip.zerver_message AS message
         WHERE visible.public_history AND message.realm_id = $2
           AND message.recipient_id = visible.recipient_id AND message.is_channel_message
         GROUP BY message.subject
         UNION ALL
         SELECT message.subject, max(message.id)::bigint AS max_id
         FROM zulip.zerver_message AS message
         JOIN zulip.zerver_usermessage AS own ON own.message_id = message.id
         WHERE NOT visible.public_history AND own.user_profile_id = $1
           AND message.realm_id = $2 AND message.recipient_id = visible.recipient_id
           AND message.is_channel_message
         GROUP BY message.subject
     ) AS history ON TRUE
     ORDER BY history.max_id DESC"
);

// Both draft and status reads use the same test for shared active recipients.
// Stream subscriptions require active users; direct-message groups retain
// membership independently of a participant's stream subscription state.
macro_rules! shares_recipient {
    () => {
        "EXISTS (
                   SELECT 1 FROM zulip.zerver_subscription AS target
                   JOIN zulip.zerver_subscription AS own USING (recipient_id)
                   JOIN zulip.zerver_recipient AS r ON r.id = own.recipient_id
                   WHERE target.user_profile_id = u.id AND target.active
                     AND own.user_profile_id = actor.id AND own.active
                     AND (r.type = 3 OR (r.type = 2 AND target.is_user_active))
               )"
    };
}

pub(crate) const DIRECT_RECIPIENTS_SQL: &str = concat!(
    "
    WITH RECURSIVE actor AS (
        SELECT %s::integer AS id, %s::integer AS realm_id, %s::boolean AS is_guest
    ), member_groups(id) AS (
        SELECT m.user_group_id FROM zulip.zerver_usergroupmembership AS m
        JOIN actor ON actor.id = m.user_profile_id
        JOIN zulip.zerver_userprofile AS sender ON sender.id = actor.id
        WHERE actor.is_guest AND sender.is_active
        UNION
        SELECT m.supergroup_id FROM zulip.zerver_groupgroupmembership AS m
        JOIN member_groups AS g ON g.id = m.subgroup_id
    )
    SELECT u.id, u.realm_id, u.delivery_email, u.email,
           (NOT u.is_active AND NOT u.is_mirror_dummy) OR realm.deactivated,
           CASE
               WHEN u.is_bot OR NOT actor.is_guest THEN TRUE
               WHEN EXISTS (
                   SELECT 1 FROM member_groups AS g
                   WHERE g.id = sender_realm.can_access_all_users_group_id
               ) THEN TRUE
               ELSE ",
    shares_recipient!(),
    "
           END
    FROM zulip.zerver_userprofile AS u
    JOIN zulip.zerver_realm AS realm ON realm.id = u.realm_id
    CROSS JOIN actor
    JOIN zulip.zerver_realm AS sender_realm ON sender_realm.id = actor.realm_id
    WHERE u.id = ANY(%s::bigint[])
"
);

#[cfg(feature = "http")]
pub(crate) use shares_recipient;

pub(crate) struct DirectRecipient {
    pub id: i32,
    pub realm_id: i32,
    pub delivery_email: String,
    pub email: String,
    pub deactivated: bool,
    pub accessible: bool,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum DirectRecipientError<'a> {
    InvalidId(i64),
    Inaccessible,
    Deactivated(&'a str),
    CrossRealm,
}

pub(crate) fn validate_direct_recipients<'a>(
    user_id: i32,
    realm_id: i32,
    email: &str,
    ids: &[i64],
    cross_realm_bot_emails: &HashSet<String>,
    recipients: &'a [DirectRecipient],
) -> Result<(), DirectRecipientError<'a>> {
    let is_cross_realm_bot = |email: &str| cross_realm_bot_emails.contains(&email.to_lowercase());
    let by_id: HashMap<_, _> = recipients
        .iter()
        .map(|row| (i64::from(row.id), row))
        .collect();
    let mut resolved = Vec::with_capacity(ids.len());
    for id in ids {
        let row = by_id
            .get(id)
            .filter(|row| row.realm_id == realm_id || is_cross_realm_bot(&row.delivery_email))
            .ok_or(DirectRecipientError::InvalidId(*id))?;
        resolved.push(*row);
    }

    if ids != [i64::from(user_id)] && resolved.iter().any(|row| !row.accessible) {
        return Err(DirectRecipientError::Inaccessible);
    }

    let mut realms = HashSet::new();
    if !is_cross_realm_bot(email) {
        realms.insert(realm_id);
    }
    for row in resolved {
        if row.deactivated {
            return Err(DirectRecipientError::Deactivated(&row.email));
        }
        if !is_cross_realm_bot(&row.email) {
            realms.insert(row.realm_id);
        }
    }
    if realms.len() > 1 {
        return Err(DirectRecipientError::CrossRealm);
    }
    Ok(())
}
