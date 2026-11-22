//! Read a channel roster only after both Django visibility checks.

use tokio_postgres::{Client, Error};

use super::channel_metadata::{channel_metadata_group_match, channel_metadata_visible};

pub(super) const PATH: &str = "/api/v1/streams/{id}/members";
pub(super) const SESSION_PATH: &str = "/json/streams/{id}/members";

// The first gate is channel metadata, including archived channels. The
// subscriber gate independently uses the typed stream recipient check.
const SQL: &str = concat!(
    "WITH RECURSIVE actor AS (
         SELECT id, realm_id, role FROM zulip.zerver_userprofile
         WHERE id = $1 AND realm_id = $2 AND is_active
     ), member_groups(id) AS (
         SELECT m.user_group_id FROM zulip.zerver_usergroupmembership AS m
         JOIN actor ON actor.id = m.user_profile_id WHERE actor.role <> 600
         UNION
         SELECT m.supergroup_id FROM zulip.zerver_groupgroupmembership AS m
         JOIN member_groups AS g ON g.id = m.subgroup_id
     ), available AS (
         SELECT s.id, s.is_web_public, s.invite_only, actor.role,
             EXISTS (
                 SELECT 1 FROM zulip.zerver_subscription AS sub
                 JOIN zulip.zerver_recipient AS r ON r.id = sub.recipient_id
                 WHERE sub.user_profile_id = actor.id AND sub.active
                   AND r.type = 2 AND r.type_id = s.id
             ) AS subscribed,
             (actor.role <> 600 AND ",
    channel_metadata_group_match!(),
    ") AS permitted_group
         FROM zulip.zerver_stream AS s CROSS JOIN actor
         WHERE s.realm_id = actor.realm_id AND s.id = $3::bigint
           AND ",
    channel_metadata_visible!(),
    "
     ), roster AS (
         SELECT id, CASE
             WHEN is_web_public OR (role = 600 AND subscribed) THEN 'visible'
             WHEN role = 600 AND NOT invite_only THEN 'public_unavailable'
             WHEN role IN (100, 200) OR permitted_group THEN 'visible'
             WHEN invite_only AND NOT subscribed THEN 'private_unavailable'
             ELSE 'visible'
         END AS visibility FROM available
     )
     SELECT visibility, CASE WHEN visibility = 'visible' THEN ARRAY(
         SELECT sub.user_profile_id FROM zulip.zerver_subscription AS sub
         JOIN zulip.zerver_recipient AS r ON r.id = sub.recipient_id
         WHERE r.type = 2 AND r.type_id = roster.id AND sub.active AND sub.is_user_active
         ORDER BY sub.user_profile_id
     ) END AS subscribers
     FROM roster"
);

pub(super) enum Outcome {
    InvalidChannel,
    PublicUnavailable,
    PrivateUnavailable,
    Visible(Vec<i32>),
}

pub(super) async fn read(
    database: &Client,
    viewer: i32,
    realm: i32,
    channel: i64,
) -> Result<Outcome, Error> {
    let Some(row) = database
        .query_opt(SQL, &[&viewer, &realm, &channel])
        .await?
    else {
        return Ok(Outcome::InvalidChannel);
    };
    Ok(match row.try_get::<_, &str>("visibility")? {
        "visible" => Outcome::Visible(row.try_get("subscribers")?),
        "public_unavailable" => Outcome::PublicUnavailable,
        _ => Outcome::PrivateUnavailable,
    })
}
