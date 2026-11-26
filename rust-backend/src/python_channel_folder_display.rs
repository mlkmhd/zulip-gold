//! Stable display ordering of already selected channel folder rows.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString, PyType};

#[pyfunction]
pub(crate) fn plan_channel_folder_display_order(keys: &Bound<'_, PyAny>) -> Option<Vec<usize>> {
    let keys = keys.cast_exact::<PyList>().ok()?;
    let mut indexed = keys
        .iter()
        .enumerate()
        .map(|(position, key)| {
            let key = key.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
            Some((key, position))
        })
        .collect::<Option<Vec<_>>>()?;
    // First compare the selected sort key, then the original index.
    indexed.sort_unstable();
    Some(indexed.into_iter().map(|(_, position)| position).collect())
}

#[pyfunction]
pub(crate) fn assemble_channel_folder_payloads<'py>(
    folders: &Bound<'py, PyAny>,
    folder_type: &Bound<'py, PyType>,
) -> PyResult<Option<Vec<Bound<'py, PyDict>>>> {
    let Ok(folders) = folders.cast_exact::<PyList>() else {
        return Ok(None);
    };
    let mut payloads = Vec::with_capacity(folders.len());
    for folder in folders.iter() {
        if !folder.get_type().is(folder_type) {
            return Ok(None);
        }
        let payload = PyDict::new(folder.py());
        for field in [
            "id",
            "name",
            "description",
            "rendered_description",
            "order",
            "creator_id",
            "date_created",
            "is_archived",
        ] {
            let value = folder.getattr(field)?;
            let ordinary = match field {
                "name" | "description" | "rendered_description" => {
                    value.is_exact_instance_of::<PyString>()
                }
                "is_archived" => value.is_exact_instance_of::<PyBool>(),
                "creator_id" => value.is_none() || value.is_exact_instance_of::<PyInt>(),
                _ => value.is_exact_instance_of::<PyInt>(),
            };
            if !ordinary {
                return Ok(None);
            }
            payload.set_item(field, value)?;
        }
        payloads.push(payload);
    }
    Ok(Some(payloads))
}
