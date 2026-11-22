//! Read topic history visible to an authenticated API client.

use std::collections::HashSet;

use serde::Serialize;
use tokio_postgres::{Client, Error};

use super::unicode_lowercase::UnicodeLowercase;
use crate::draft_recipients::CHANNEL_TOPIC_HISTORY_SQL;

pub(super) const PATH: &str = "/api/v1/users/me/{id}/topics";
pub(super) const ALLOW_EMPTY: &str = "allow_empty_topic_name";

#[derive(Serialize)]
pub(super) struct Topic {
    name: String,
    max_id: i64,
}

pub(super) async fn read(
    database: &Client,
    user_id: i32,
    realm_id: i32,
    channel_id: i64,
    allow_empty: bool,
    lowercase: &UnicodeLowercase,
) -> Result<Option<Vec<Topic>>, Error> {
    let rows = database
        .query(
            CHANNEL_TOPIC_HISTORY_SQL,
            &[&user_id, &realm_id, &channel_id],
        )
        .await?;
    if rows.is_empty() {
        return Ok(None);
    }

    // The newest spelling for each Python-lowercased topic wins. Replace the
    // empty display name only after grouping; a literal "general chat" is distinct.
    let mut seen = HashSet::new();
    let mut topics = Vec::new();
    for row in rows {
        let Some(name) = row.try_get::<_, Option<String>>(0)? else {
            continue;
        };
        if seen.insert(lowercase.lower(&name)) {
            let name = if name.is_empty() && !allow_empty {
                "general chat".to_owned()
            } else {
                name
            };
            topics.push(Topic {
                name,
                max_id: row.try_get(1)?,
            });
        }
    }
    Ok(Some(topics))
}
