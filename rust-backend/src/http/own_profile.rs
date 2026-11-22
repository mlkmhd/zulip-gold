//! Unrouted self-profile projection. It does not authenticate or select other users.

use tokio_postgres::{Error, GenericClient};

use super::own_profile_presentation::{Presentation, avatar_kind, system_bot};
use crate::own_profile_payload::{CustomField, Facts, assemble};

pub use crate::own_profile_payload::OwnProfile;

const PROFILE: &str = r#"
    SELECT u.id, u.email, u.delivery_email, u.full_name, u.avatar_source, u.avatar_version,
           u.role, u.is_bot, u.bot_type, u.bot_owner_id, u.is_active,
           u.timezone, u.is_imported_stub, u.is_deleted,
           to_char(u.date_joined AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI') || '+00:00' AS joined,
           COALESCE(
               (SELECT um.message_id::bigint
                  FROM zulip.zerver_usermessage AS um
                 WHERE um.user_profile_id = u.id
              ORDER BY um.message_id DESC
                 LIMIT 1),
               -1::bigint
           ) AS max_message_id
      FROM zulip.zerver_userprofile AS u
     WHERE u.id = $1 AND u.realm_id = $2 AND u.is_active
"#;

const CUSTOM_VALUES: &str = "
    SELECT field.id, field.field_type, field_value.value, field_value.rendered_value
      FROM zulip.zerver_customprofilefieldvalue AS field_value
      JOIN zulip.zerver_customprofilefield AS field ON field.id = field_value.field_id
     WHERE field_value.user_profile_id = $1 AND field.realm_id = $2";

#[derive(Debug)]
pub enum ReadError {
    Database(Error),
    AvatarContextMismatch,
}

impl From<Error> for ReadError {
    fn from(error: Error) -> Self {
        Self::Database(error)
    }
}

/// Project GET /users/me data for a principal already authenticated in the
/// caller's realm. Both IDs must come from authentication, not the URL.
/// The query returns no result if the account is no longer active in that realm.
pub async fn read_authenticated(
    db: &impl GenericClient,
    authenticated_user_id: i32,
    authenticated_realm_id: i32,
    presentation: Presentation<'_>,
) -> Result<Option<OwnProfile>, ReadError> {
    let args: &[&(dyn tokio_postgres::types::ToSql + Sync)] =
        &[&authenticated_user_id, &authenticated_realm_id];
    let Some(row) = db.query_opt(PROFILE, args).await? else {
        return Ok(None);
    };
    let is_bot: bool = row.try_get("is_bot")?;
    let raw_timezone: String = row.try_get("timezone")?;
    let timezone = presentation
        .timezone_aliases
        .canonical(&raw_timezone)
        .to_owned();
    let email: String = row.try_get("email")?;
    let delivery_email: String = row.try_get("delivery_email")?;
    let avatar_source: String = row.try_get("avatar_source")?;
    let avatar_version = row.try_get("avatar_version")?;
    if presentation.avatar.user_id != authenticated_user_id
        || presentation.avatar.realm_id != authenticated_realm_id
        || presentation.avatar.version != avatar_version
        || presentation.avatar.kind
            != avatar_kind(
                &delivery_email,
                &avatar_source,
                presentation.system_bot_emails,
            )
    {
        return Err(ReadError::AvatarContextMismatch);
    }

    let mut fields: Vec<CustomField> = Vec::new();
    if !is_bot {
        for field in db.query(CUSTOM_VALUES, args).await? {
            fields.push((
                field.try_get("id")?,
                field.try_get("field_type")?,
                field.try_get("value")?,
                field.try_get("rendered_value")?,
            ));
        }
    }
    let is_system_bot = is_bot && system_bot(&email, presentation.system_bot_emails);
    Ok(Some(assemble(
        Facts {
            user_id: row.try_get("id")?,
            email,
            // Django's delivery-email visibility rule always permits the user themself.
            delivery_email,
            full_name: row.try_get("full_name")?,
            avatar_source,
            avatar_version,
            role: row.try_get("role")?,
            is_bot,
            is_active: row.try_get("is_active")?,
            date_joined: row.try_get("joined")?,
            is_imported_stub: row.try_get("is_imported_stub")?,
            is_deleted: row.try_get("is_deleted")?,
            max_message_id: row.try_get("max_message_id")?,
        },
        presentation.avatar.url.to_owned(),
        timezone,
        row.try_get("bot_type")?,
        row.try_get("bot_owner_id")?,
        is_system_bot,
        fields,
    )))
}
