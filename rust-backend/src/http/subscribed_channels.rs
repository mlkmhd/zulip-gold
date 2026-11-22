//! List target subscriptions visible under the authenticated metadata policy.

use tokio_postgres::{Client, Error};

use super::{channel_metadata::channel_metadata_visible, visible_target::visible_target_ctes};
use crate::draft_recipients::shares_recipient;

pub(super) const PATH: &str = "/api/v1/users/{user_id}/channels";
pub(super) const SESSION_PATH: &str = "/json/users/{user_id}/channels";

const SQL: &str = concat!(
    visible_target_ctes!("", "u.is_bot OR ", ""),
    "SELECT target.visibility, CASE WHEN target.visibility = 'visible' THEN ARRAY(
         SELECT s.id::bigint FROM zulip.zerver_stream AS s
         JOIN zulip.zerver_subscription AS sub ON sub.recipient_id = s.recipient_id
         JOIN zulip.zerver_recipient AS r ON r.id = sub.recipient_id
         WHERE s.realm_id = actor.realm_id
           AND sub.user_profile_id = target.id AND sub.active AND r.type = 2
           AND ",
    channel_metadata_visible!(),
    "
         ORDER BY s.id
     ) END AS subscribed_channel_ids
     FROM target CROSS JOIN actor"
);

pub(super) enum Outcome {
    NoSuchUser,
    DeactivatedUser,
    RestrictedUser,
    Visible(Vec<i64>),
}

pub(super) async fn read(
    database: &Client,
    viewer: i32,
    realm: i32,
    target: i32,
) -> Result<Outcome, Error> {
    let Some(row) = database.query_opt(SQL, &[&viewer, &realm, &target]).await? else {
        return Ok(Outcome::NoSuchUser);
    };
    Ok(match row.try_get::<_, &str>("visibility")? {
        "deactivated" => Outcome::DeactivatedUser,
        "visible" => Outcome::Visible(row.try_get("subscribed_channel_ids")?),
        _ => Outcome::RestrictedUser,
    })
}
