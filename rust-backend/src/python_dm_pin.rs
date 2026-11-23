//! Change only a Django-authorized user's existing direct-message subscription.

use pyo3::prelude::*;

use crate::direct_message_group::member_hash;

const PIN_EXISTING: &str = "
    WITH target AS MATERIALIZED (
        SELECT recipient_id FROM zulip.zerver_huddle
        WHERE huddle_hash = %s AND group_size = %s AND recipient_id IS NOT NULL
    ), locked AS MATERIALIZED (
        SELECT sub.id, sub.pin_to_top
        FROM target JOIN zulip.zerver_subscription AS sub
            ON sub.recipient_id = target.recipient_id
        WHERE sub.user_profile_id = %s
        FOR NO KEY UPDATE OF sub
    ), updated AS (
        UPDATE zulip.zerver_subscription AS sub SET pin_to_top = %s
        FROM locked
        WHERE sub.id = locked.id AND sub.user_profile_id = %s
            AND locked.pin_to_top <> %s
        RETURNING sub.id
    )
    SELECT CASE
        WHEN EXISTS (SELECT 1 FROM updated) THEN 'changed'
        WHEN NOT EXISTS (SELECT 1 FROM target) THEN 'missing_conversation'
        WHEN NOT EXISTS (SELECT 1 FROM locked) THEN 'missing_subscription'
        WHEN EXISTS (SELECT 1 FROM locked WHERE pin_to_top = %s) THEN 'unchanged'
        ELSE 'not_updated'
    END
";

#[pyfunction]
pub(crate) fn set_existing_dm_pin(
    cursor: &Bound<'_, PyAny>,
    actor: i32,
    member_ids: Vec<i32>,
    pinned: bool,
) -> PyResult<String> {
    cursor.call_method1(
        "execute",
        (
            PIN_EXISTING,
            (
                member_hash(&member_ids),
                member_ids.len(),
                actor,
                pinned,
                actor,
                pinned,
                pinned,
            ),
        ),
    )?;
    let (result,): (String,) = cursor.call_method0("fetchone")?.extract()?;
    Ok(result)
}
