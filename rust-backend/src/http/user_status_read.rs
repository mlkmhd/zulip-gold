//! The authenticated status projection on both read-only API surfaces.

use serde_json::{Map, Value};
use tokio_postgres::{Client, Error};

use crate::{draft_recipients::shares_recipient, user_status::UserStatus};

pub(super) const PATH: &str = "/api/v1/users/{id}/status";
pub(super) const SESSION_PATH: &str = "/json/users/{id}/status";

// Match access_user_by_id before projecting status: same realm, human
// target, active target, then Django's Everyone/Members visibility and
// common recipient rules. Status fields are joined only for visible users.
const USER_STATUS_READ_SQL: &str = concat!(
    "WITH RECURSIVE actor AS (
         SELECT id, realm_id, role FROM zulip.zerver_userprofile
         WHERE id = $1 AND realm_id = $2 AND is_active
     ), member_groups(id) AS (
         SELECT m.user_group_id FROM zulip.zerver_usergroupmembership AS m
         JOIN actor ON actor.id = m.user_profile_id WHERE actor.role = 600
         UNION
         SELECT m.supergroup_id FROM zulip.zerver_groupgroupmembership AS m
         JOIN member_groups AS g ON g.id = m.subgroup_id
     ), target AS (
         SELECT u.id, u.presence_enabled, CASE
             WHEN NOT u.is_active THEN 'deactivated'
             WHEN u.id = actor.id OR actor.role <> 600 OR policy.name = 'role:everyone'
               OR EXISTS (
                   SELECT 1 FROM member_groups AS g WHERE g.id = policy.usergroup_ptr_id
               )
               OR ",
    shares_recipient!(),
    " THEN 'visible'
             ELSE 'restricted'
         END AS visibility
         FROM zulip.zerver_userprofile AS u CROSS JOIN actor
         JOIN zulip.zerver_realm AS realm ON realm.id = actor.realm_id
         JOIN zulip.zerver_namedusergroup AS policy
           ON policy.usergroup_ptr_id = realm.can_access_all_users_group_id
          AND policy.realm_id = realm.id AND policy.is_system_group
          AND policy.name IN ('role:everyone', 'role:members')
         WHERE u.id = $3 AND u.realm_id = actor.realm_id AND NOT u.is_bot
     )
     SELECT target.visibility, target.presence_enabled,
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
