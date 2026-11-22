//! Authenticated administrator read of a realm's existing export-consent flags.

use serde::Serialize;
use tokio_postgres::{Client, Error};

pub(super) const PATH: &str = "/api/v1/export/realm/consents";
pub(super) const SESSION_PATH: &str = "/json/export/realm/consents";

// Use the authenticated actor's live role and realm in the same statement as
// the projection. Django is_realm_admin includes owners (100) and admins
// (200), not moderators. Do not read emails, content, or export paths.
const SQL: &str = "
    WITH administrator AS (
        SELECT realm_id FROM zulip.zerver_userprofile
        WHERE id = $1 AND realm_id = $2 AND is_active AND role IN (100, 200)
    )
    SELECT subject.id, subject.allow_private_data_export, subject.email_address_visibility
    FROM administrator
    LEFT JOIN zulip.zerver_userprofile AS subject
        ON subject.realm_id = administrator.realm_id AND subject.is_active AND NOT subject.is_bot
    ORDER BY subject.id
";

#[derive(Serialize)]
pub(super) struct ExportConsent {
    user_id: i32,
    consented: bool,
    email_address_visibility: i16,
}

pub(super) async fn read(
    database: &Client,
    user_id: i32,
    realm_id: i32,
) -> Result<Option<Vec<ExportConsent>>, Error> {
    let rows = database.query(SQL, &[&user_id, &realm_id]).await?;
    if rows.is_empty() {
        return Ok(None);
    }
    let mut consents = Vec::with_capacity(rows.len());
    for row in rows {
        if let Some(user_id) = row.try_get(0)? {
            consents.push(ExportConsent {
                user_id,
                consented: row.try_get(1)?,
                email_address_visibility: row.try_get(2)?,
            });
        }
    }
    Ok(Some(consents))
}
