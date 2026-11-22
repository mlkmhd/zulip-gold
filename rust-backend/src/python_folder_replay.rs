//! Reconcile already selected channel-folder events for Django register.

use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString};

use crate::python_schedule_replay::{fields, int_field};

fn rows_with_ids<'py>(
    rows: impl IntoIterator<Item = Bound<'py, PyAny>>,
) -> Option<Vec<(i64, Bound<'py, PyAny>)>> {
    rows.into_iter()
        .map(|row| Some((int_field(&row, "id")?, row)))
        .collect()
}

fn sort_after_add<'py>(
    rows: &Bound<'py, PyList>,
    event: &Bound<'py, PyDict>,
) -> Option<Vec<Bound<'py, PyAny>>> {
    let incoming = event.get_item("channel_folder").ok()??;
    let mut ordered = rows_with_ids(rows.iter().chain([incoming]))?;
    // Stable sort preserves the original order of folders with equal IDs.
    ordered.sort_by_key(|(id, _)| *id);
    Some(ordered.into_iter().map(|(_, row)| row).collect())
}

fn plan_update<'py>(
    rows: &Bound<'py, PyList>,
    event: &Bound<'py, PyDict>,
) -> Option<(Vec<Bound<'py, PyAny>>, Bound<'py, PyDict>)> {
    let id = int_field(event.as_any(), "channel_folder_id")?;
    let patch = event.get_item("data").ok()??;
    let patch = fields(&patch)?;
    // No action changes the identifier. Let Python do any legacy update
    // (including exotic mappings) before we mutate any folder ourselves.
    if patch.contains("id").ok()? {
        return None;
    }
    let selected = rows_with_ids(rows.iter())?
        .into_iter()
        .filter_map(|(candidate, row)| (candidate == id).then_some(row))
        .collect();
    Some((selected, patch.clone()))
}

fn plan_reorder<'py>(
    rows: &Bound<'py, PyList>,
    event: &Bound<'py, PyDict>,
) -> Option<Vec<(usize, Bound<'py, PyAny>)>> {
    let order = event.get_item("order").ok()??;
    let order = order.cast_exact::<PyList>().ok()?;
    let mut positions = HashMap::new();
    for (index, id) in order.iter().enumerate() {
        let id = id.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
        // Like the Python dict comprehension, a duplicate uses its last index.
        positions.insert(id, index);
    }
    let mut ordered = rows_with_ids(rows.iter())?
        .into_iter()
        .map(|(id, row)| Some((*positions.get(&id)?, row)))
        .collect::<Option<Vec<_>>>()?;
    ordered.sort_by_key(|(index, _)| *index);
    Some(ordered)
}

#[pyfunction]
pub(crate) fn apply_channel_folder_event<'py>(
    py: Python<'py>,
    selected: &Bound<'py, PyAny>,
    event: &Bound<'py, PyAny>,
) -> PyResult<bool> {
    let (Ok(rows), Some(event)) = (selected.cast_exact::<PyList>(), fields(event)) else {
        return Ok(false);
    };
    let (Some(kind), Some(op)) = (event.get_item("type")?, event.get_item("op")?) else {
        return Ok(false);
    };
    let (Ok(kind), Ok(op)) = (kind.cast_exact::<PyString>(), op.cast_exact::<PyString>()) else {
        return Ok(false);
    };
    if kind.to_str().ok() != Some("channel_folder") {
        return Ok(false);
    }

    match op.to_str().ok() {
        Some("add") => {
            let Some(sorted) = sort_after_add(rows, event) else {
                return Ok(false);
            };
            let sorted = PyList::new(py, sorted)?;
            rows.set_slice(0, rows.len(), sorted.as_any())?;
        }
        Some("update") => {
            let Some((selected, patch)) = plan_update(rows, event) else {
                return Ok(false);
            };
            for row in selected {
                row.cast::<PyDict>()?.update(patch.as_mapping())?;
            }
        }
        Some("reorder") => {
            let Some(ordered) = plan_reorder(rows, event) else {
                return Ok(false);
            };
            let sorted = PyList::new(py, ordered.iter().map(|(_, row)| row))?;
            for (index, row) in ordered {
                row.cast::<PyDict>()?.set_item("order", index)?;
            }
            rows.set_slice(0, rows.len(), sorted.as_any())?;
        }
        _ => return Ok(false),
    }
    Ok(true)
}
