//! Read receipts authorised against a single live database snapshot.

use tokio_postgres::{Client, Error};

pub(super) const PATH: &str = "/api/v1/messages/{id}/read_receipts";
pub(super) const SESSION_PATH: &str = "/json/messages/{id}/read_receipts";

// SQL follows message.has_message_access for reads, not the looser channel
// metadata or channel browsing rules. A direct message requires a receipt;
// public history is available to non-guests, and restricted history requires
// both current content access and the original UserMessage row.
// Recipient.STREAM = 2, UserProfile.ROLE_GUEST = 600, UserMessage.flags.read = 1.
const SQL: &str = "
    WITH RECURSIVE actor AS (
        SELECT id, realm_id, role FROM zulip.zerver_userprofile
        WHERE id = $1 AND realm_id = $2 AND is_active
    ), member_groups(id) AS (
        SELECT m.user_group_id FROM zulip.zerver_usergroupmembership AS m
        JOIN actor ON actor.id = m.user_profile_id WHERE actor.role <> 600
        UNION
        SELECT m.supergroup_id FROM zulip.zerver_groupgroupmembership AS m
        JOIN member_groups AS g ON g.id = m.subgroup_id
    ), visible AS (
        SELECT m.id, m.sender_id, r.enable_read_receipts, actor.id AS viewer_id
        FROM zulip.zerver_message AS m
        JOIN zulip.zerver_recipient AS recipient ON recipient.id = m.recipient_id
        CROSS JOIN actor
        JOIN zulip.zerver_realm AS r ON r.id = actor.realm_id
        LEFT JOIN zulip.zerver_stream AS s
            ON recipient.type = 2 AND s.id = recipient.type_id
        CROSS JOIN LATERAL (
            SELECT EXISTS (
                SELECT 1 FROM zulip.zerver_usermessage AS own
                WHERE own.message_id = m.id AND own.user_profile_id = actor.id
            ) AS has_receipt
        ) AS own
        WHERE m.id = $3::bigint AND CASE WHEN recipient.type <> 2 THEN own.has_receipt
          ELSE s.realm_id = actor.realm_id AND (
            (NOT s.invite_only AND actor.role <> 600) OR (
              (s.history_public_to_subscribers OR own.has_receipt)
              AND (EXISTS (
                SELECT 1 FROM zulip.zerver_subscription AS sub
                WHERE sub.user_profile_id = actor.id
                  AND sub.recipient_id = s.recipient_id AND sub.active
              ) OR EXISTS (
                SELECT 1 FROM member_groups AS g
                WHERE g.id IN (s.can_subscribe_group_id, s.can_add_subscribers_group_id)
              ))
            )
          ) END
    )
    SELECT CASE WHEN v.enable_read_receipts THEN ARRAY(
        SELECT um.user_profile_id FROM zulip.zerver_usermessage AS um
        JOIN zulip.zerver_userprofile AS reader ON reader.id = um.user_profile_id
        WHERE um.message_id = v.id AND reader.is_active AND reader.send_read_receipts
          AND (um.flags & 1) <> 0 AND um.user_profile_id <> v.sender_id
          AND NOT EXISTS (
              SELECT 1 FROM zulip.zerver_muteduser AS mute
              WHERE (mute.user_profile_id = v.viewer_id AND mute.muted_user_id = reader.id)
                 OR (mute.user_profile_id = reader.id AND mute.muted_user_id = v.viewer_id)
          )
        ORDER BY um.user_profile_id
    ) END
    FROM visible AS v
";

pub(super) enum Outcome {
    NoMessage,
    Disabled,
    Visible(Vec<i32>),
}

pub(super) async fn read(
    database: &Client,
    viewer_id: i32,
    realm_id: i32,
    message_id: i64,
) -> Result<Outcome, Error> {
    let Some(row) = database
        .query_opt(SQL, &[&viewer_id, &realm_id, &message_id])
        .await?
    else {
        return Ok(Outcome::NoMessage);
    };
    Ok(match row.try_get(0)? {
        Some(users) => Outcome::Visible(users),
        None => Outcome::Disabled,
    })
}
