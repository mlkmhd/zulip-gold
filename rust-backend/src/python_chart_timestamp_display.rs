//! Integer display values from Python-prepared chart timestamps.

use pyo3::prelude::*;
use pyo3::types::{PyFloat, PyList};

#[pyfunction]
pub(crate) fn truncate_chart_timestamp_display(values: &Bound<'_, PyAny>) -> Option<Vec<i64>> {
    const I64_UPPER_EXCLUSIVE: f64 = 9_223_372_036_854_775_808.0;
    values
        .cast_exact::<PyList>()
        .ok()?
        .iter()
        .map(|value| {
            let value = value.cast_exact::<PyFloat>().ok()?.value();
            if !(-I64_UPPER_EXCLUSIVE..I64_UPPER_EXCLUSIVE).contains(&value) {
                return None;
            }
            Some(value as i64)
        })
        .collect()
}
