//! Unrouted self-profile projection. It does not authenticate or select other users.

use std::collections::BTreeMap;

use serde::Serialize;
use tokio_postgres::{Error, GenericClient};

use super::own_profile_presentation::{Presentation, avatar_kind, system_bot};

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

// Django's UserProfile and CustomProfileField constants.
const ROLE_OWNER: i16 = 100;
const ROLE_ADMINISTRATOR: i16 = 200;
const ROLE_GUEST: i16 = 600;
const FIELD_SHORT_TEXT: i16 = 1;
const FIELD_PARAGRAPH: i16 = 2;

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

#[derive(Debug, Serialize)]
pub struct OwnProfile {
    user_id: i32,
    email: String,
    delivery_email: String,
    full_name: String,
    avatar_source: String,
    avatar_version: i16,
    avatar_url: String,
    role: i16,
    is_admin: bool,
    is_owner: bool,
    is_guest: bool,
    is_bot: bool,
    is_active: bool,
    timezone: String,
    date_joined: String,
    is_imported_stub: bool,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    is_deleted: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    profile_data: Option<BTreeMap<i64, FieldValue>>,
    #[serde(flatten)]
    bot: Option<BotData>,
    max_message_id: i64,
}

#[derive(Debug, Serialize)]
struct BotData {
    bot_type: Option<i16>,
    bot_owner_id: Option<i32>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    is_system_bot: bool,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
enum FieldValue {
    Rendered {
        value: String,
        rendered_value: Option<String>,
    },
    Plain {
        value: String,
    },
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
    let role = row.try_get("role")?;
    let is_bot = row.try_get("is_bot")?;
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

    let (bot, profile_data) = if is_bot {
        (
            Some(BotData {
                bot_type: row.try_get("bot_type")?,
                bot_owner_id: row.try_get("bot_owner_id")?,
                is_system_bot: system_bot(&email, presentation.system_bot_emails),
            }),
            None,
        )
    } else {
        let mut fields = BTreeMap::new();
        for field in db.query(CUSTOM_VALUES, args).await? {
            let value = field.try_get("value")?;
            let data = match field.try_get::<_, i16>("field_type")? {
                FIELD_SHORT_TEXT | FIELD_PARAGRAPH => FieldValue::Rendered {
                    value,
                    rendered_value: field.try_get("rendered_value")?,
                },
                _ => FieldValue::Plain { value },
            };
            fields.insert(field.try_get("id")?, data);
        }
        (None, Some(fields))
    };

    Ok(Some(OwnProfile {
        user_id: row.try_get("id")?,
        email,
        // Django's delivery-email visibility rule always permits the user themself.
        delivery_email,
        full_name: row.try_get("full_name")?,
        avatar_source,
        avatar_version,
        avatar_url: presentation.avatar.url.to_owned(),
        role,
        is_admin: matches!(role, ROLE_OWNER | ROLE_ADMINISTRATOR),
        is_owner: role == ROLE_OWNER,
        is_guest: role == ROLE_GUEST,
        is_bot,
        is_active: row.try_get("is_active")?,
        timezone,
        date_joined: row.try_get("joined")?,
        is_imported_stub: row.try_get("is_imported_stub")?,
        is_deleted: row.try_get("is_deleted")?,
        profile_data,
        bot,
        max_message_id: row.try_get("max_message_id")?,
    }))
}
