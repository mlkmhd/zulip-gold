//! Read the ID of a channel whose content the authenticated caller can access.

use tokio_postgres::{Client, Error};

use crate::draft_recipients::CHANNEL_ID_BY_NAME_SQL;

pub(super) const PATH: &str = "/api/v1/get_stream_id";
pub(super) const SESSION_PATH: &str = "/json/get_stream_id";
pub(super) const STREAM: &str = "stream";

pub(super) async fn find(
    db: &Client,
    user_id: i32,
    realm_id: i32,
    name: &str,
) -> Result<Option<i64>, Error> {
    // Python str.strip includes these four separators in addition to the
    // Unicode White_Space property. The database performs Django's iexact.
    let name =
        name.trim_matches(|ch: char| ch.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&ch));
    db.query_opt(CHANNEL_ID_BY_NAME_SQL, &[&user_id, &realm_id, &name])
        .await?
        .map(|row| row.try_get(0))
        .transpose()
}
