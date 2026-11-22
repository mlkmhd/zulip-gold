//! A single subscription flag after user visibility and channel metadata checks.

use tokio_postgres::{Client, Error};

use super::{channel_metadata::channel_metadata_visible, visible_target::visible_target_ctes};
use crate::draft_recipients::shares_recipient;

pub(super) const PATH: &str = "/api/v1/users/{user_id}/subscriptions/{stream_id}";
pub(super) const SESSION_PATH: &str = "/json/users/{user_id}/subscriptions/{stream_id}";

const SQL: &str = concat!(
    visible_target_ctes!("", "u.is_bot OR ", ""),
    "SELECT target.visibility, CASE WHEN channel.id IS NOT NULL THEN EXISTS (
         SELECT 1 FROM zulip.zerver_subscription AS sub
         JOIN zulip.zerver_recipient AS r ON r.id = sub.recipient_id
         WHERE sub.user_profile_id = target.id AND sub.active
           AND r.type = 2 AND r.type_id = channel.id
     ) END AS subscribed
     FROM target CROSS JOIN actor LEFT JOIN LATERAL (
         SELECT s.id FROM zulip.zerver_stream AS s
         WHERE target.visibility = 'visible'
           AND s.realm_id = actor.realm_id AND s.id = $4::bigint AND NOT s.deactivated
           AND ",
    channel_metadata_visible!(),
    "
     ) AS channel ON TRUE"
);

pub(super) enum Outcome {
    NoSuchUser,
    DeactivatedUser,
    RestrictedUser,
    InvalidChannel,
    Subscribed(bool),
}

pub(super) async fn read(
    database: &Client,
    viewer: i32,
    realm: i32,
    target: Option<i32>,
    channel: Option<i64>,
) -> Result<Outcome, Error> {
    let Some(row) = database
        .query_opt(SQL, &[&viewer, &realm, &target, &channel])
        .await?
    else {
        return Ok(Outcome::NoSuchUser);
    };
    Ok(match row.try_get::<_, &str>("visibility")? {
        "deactivated" => Outcome::DeactivatedUser,
        "visible" => match row.try_get("subscribed")? {
            Some(value) => Outcome::Subscribed(value),
            None => Outcome::InvalidChannel,
        },
        _ => Outcome::RestrictedUser,
    })
}
