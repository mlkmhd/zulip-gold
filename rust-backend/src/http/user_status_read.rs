//! The authenticated status projection on both read-only API surfaces.

use serde_json::{Map, Value};
use tokio_postgres::{Client, Error};

use super::visible_target::visible_target_ctes;
use crate::{draft_recipients::shares_recipient, user_status::UserStatus};

pub(super) const PATH: &str = "/api/v1/users/{id}/status";
pub(super) const SESSION_PATH: &str = "/json/users/{id}/status";

// Match access_user_by_id before projecting status: same realm, human
// target, active target, then Django's Everyone/Members visibility and
// common recipient rules. Status fields are joined only for visible users.
const USER_STATUS_READ_SQL: &str = concat!(
    visible_target_ctes!(" WHERE actor.role = 600", "", "AND NOT u.is_bot"),
    "     SELECT target.visibility, target.presence_enabled,
            status.status_text, status.emoji_name, status.emoji_code, status.reaction_type
     FROM target LEFT JOIN zulip.zerver_userstatus AS status
       ON target.visibility = 'visible' AND status.user_profile_id = target.id"
);

pub(super) enum ReadOutcome {
    NotFound,
    Deactivated,
    Restricted,
    Visible(Map<String, Value>),
}

pub(super) async fn read(
    database: &Client,
    viewer: i32,
    realm: i32,
    user: i32,
) -> Result<ReadOutcome, Error> {
    let Some(row) = database
        .query_opt(USER_STATUS_READ_SQL, &[&viewer, &realm, &user])
        .await?
    else {
        return Ok(ReadOutcome::NotFound);
    };
    match row.try_get::<_, &str>("visibility")? {
        "deactivated" => return Ok(ReadOutcome::Deactivated),
        "visible" => {}
        _ => return Ok(ReadOutcome::Restricted),
    }
    let mut status = Map::new();
    if let Some(text) = row.try_get::<_, Option<&str>>("status_text")? {
        let value = UserStatus::from_row(
            row.try_get("presence_enabled")?,
            text,
            row.try_get("emoji_name")?,
            row.try_get("emoji_code")?,
            row.try_get("reaction_type")?,
        );
        if value.away {
            status.insert("away".into(), true.into());
        }
        if let Some(text) = value.text {
            status.insert("status_text".into(), text.into());
        }
        if let Some(emoji) = value.emoji {
            status.insert("emoji_name".into(), emoji.name.into());
            status.insert("emoji_code".into(), emoji.code.into());
            status.insert("reaction_type".into(), emoji.reaction_type.into());
        }
    }
    Ok(ReadOutcome::Visible(status))
}
