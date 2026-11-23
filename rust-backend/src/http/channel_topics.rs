//! Read topic history visible to an authenticated API client.

use tokio_postgres::{Client, Error};

use super::unicode_lowercase::UnicodeLowercase;
use crate::{
    draft_recipients::CHANNEL_TOPIC_HISTORY_SQL,
    topic_history::{NewestTopicHistory, Topic},
};

pub(super) const PATH: &str = "/api/v1/users/me/{id}/topics";
pub(super) const ALLOW_EMPTY: &str = "allow_empty_topic_name";

pub(super) async fn read(
    database: &Client,
    user_id: i32,
    realm_id: i32,
    channel_id: i64,
    allow_empty: bool,
    lowercase: &UnicodeLowercase,
) -> Result<Option<Vec<Topic<String>>>, Error> {
    let rows = database
        .query(
            CHANNEL_TOPIC_HISTORY_SQL,
            &[&user_id, &realm_id, &channel_id],
        )
        .await?;
    if rows.is_empty() {
        return Ok(None);
    }

    let mut topics = NewestTopicHistory::new((!allow_empty).then(|| "general chat".to_owned()));
    for row in rows {
        let Some(name) = row.try_get::<_, Option<String>>(0)? else {
            continue;
        };
        let lower = lowercase.lower(&name);
        let is_empty = name.is_empty();
        topics.push(name, lower, is_empty, row.try_get(1)?);
    }
    Ok(Some(topics.finish()))
}
