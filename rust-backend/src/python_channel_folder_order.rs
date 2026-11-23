//! Reorder only the channel folders Django already selected and validated.

use pyo3::prelude::*;

const UPDATE_ORDER: &str = r#"
    WITH selected AS MATERIALIZED (
        SELECT folder.id, requested.position
          FROM unnest(%s::bigint[], %s::integer[]) WITH ORDINALITY
                    AS requested(id, position, traversal)
          JOIN zulip.zerver_channelfolder AS folder ON folder.id = requested.id
         WHERE folder.realm_id = %s
         ORDER BY requested.traversal
         FOR NO KEY UPDATE OF folder
    )
    UPDATE zulip.zerver_channelfolder AS folder
       SET "order" = selected.position
      FROM selected
     WHERE folder.id = selected.id AND folder.realm_id = %s
"#;

#[pyfunction]
pub(crate) fn update_channel_folder_order(
    cursor: &Bound<'_, PyAny>,
    realm_id: i64,
    folders: Vec<(i64, i32)>,
) -> PyResult<i64> {
    let (ids, positions): (Vec<_>, Vec<_>) = folders.into_iter().unzip();
    // Take row locks in the order of the already-evaluated Django queryset.
    cursor.call_method1(
        "execute",
        (UPDATE_ORDER, (ids, positions, realm_id, realm_id)),
    )?;
    cursor.getattr("rowcount")?.extract()
}
