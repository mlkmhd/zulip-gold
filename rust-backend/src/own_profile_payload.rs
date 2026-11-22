//! Shared response projection for a snapshot of the authenticated caller only.

use std::collections::BTreeMap;

use serde::Serialize;

// Django's UserProfile and CustomProfileField constants.
const ROLE_OWNER: i16 = 100;
const ROLE_ADMINISTRATOR: i16 = 200;
const ROLE_GUEST: i16 = 600;
const FIELD_SHORT_TEXT: i16 = 1;
const FIELD_PARAGRAPH: i16 = 2;

#[derive(Debug, Serialize)]
#[cfg_attr(feature = "python", derive(pyo3::FromPyObject))]
#[cfg_attr(feature = "python", pyo3(from_item_all))]
pub(crate) struct Facts {
    pub user_id: i32,
    pub email: String,
    pub delivery_email: String,
    pub full_name: String,
    pub avatar_source: String,
    pub avatar_version: i16,
    pub role: i16,
    pub is_bot: bool,
    pub is_active: bool,
    pub date_joined: String,
    pub is_imported_stub: bool,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub is_deleted: bool,
    pub max_message_id: i64,
}

pub(crate) type CustomField = (i64, i16, String, Option<String>);

#[derive(Debug, Serialize)]
pub struct OwnProfile {
    #[serde(flatten)]
    facts: Facts,
    avatar_url: String,
    timezone: String,
    is_admin: bool,
    is_owner: bool,
    is_guest: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    profile_data: Option<BTreeMap<i64, FieldValue>>,
    #[serde(flatten)]
    bot: Option<BotData>,
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

pub(crate) fn assemble(
    facts: Facts,
    avatar_url: String,
    timezone: String,
    bot_type: Option<i16>,
    bot_owner_id: Option<i32>,
    is_system_bot: bool,
    custom_fields: impl IntoIterator<Item = CustomField>,
) -> OwnProfile {
    let bot = facts.is_bot.then_some(BotData {
        bot_type,
        bot_owner_id,
        is_system_bot,
    });
    let profile_data = if facts.is_bot {
        None
    } else {
        Some(
            custom_fields
                .into_iter()
                .map(|(id, field_type, value, rendered_value)| {
                    let field = match field_type {
                        FIELD_SHORT_TEXT | FIELD_PARAGRAPH => FieldValue::Rendered {
                            value,
                            rendered_value,
                        },
                        _ => FieldValue::Plain { value },
                    };
                    (id, field)
                })
                .collect(),
        )
    };
    OwnProfile {
        is_admin: matches!(facts.role, ROLE_OWNER | ROLE_ADMINISTRATOR),
        is_owner: facts.role == ROLE_OWNER,
        is_guest: facts.role == ROLE_GUEST,
        facts,
        avatar_url,
        timezone,
        profile_data,
        bot,
    }
}
