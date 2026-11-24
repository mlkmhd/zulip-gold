//! Stable display ordering of already selected channel folder rows.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList};

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
