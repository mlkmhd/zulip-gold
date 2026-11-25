//! Read already loaded language display metadata in its original order.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString};

use crate::python_text_bytes::python_string_bytes;

fn plain_row<'py>(value: &Bound<'py, PyAny>) -> Option<Bound<'py, PyDict>> {
    let row = value.cast_exact::<PyDict>().ok()?;
    // Don't run custom key equality or hashing before opting into Python.
    row.iter()
        .all(|(key, _)| key.cast_exact::<PyString>().is_ok())
        .then(|| row.clone())
}

fn field<'py>(row: &Bound<'py, PyDict>, key: &str) -> Option<Bound<'py, PyString>> {
    row.get_item(key).ok()??.cast_into_exact::<PyString>().ok()
}

type Lookup<'py> = (Option<Bound<'py, PyString>>,);

#[pyfunction]
pub(crate) fn lookup_catalog_display_name<'py>(
    rows: &Bound<'py, PyAny>,
    key: &Bound<'_, PyAny>,
) -> Option<Lookup<'py>> {
    let key = python_string_bytes(key.cast_exact::<PyString>().ok()?)?;
    for row in rows.cast_exact::<PyList>().ok()?.iter() {
        let row = plain_row(&row)?;
        let code = field(&row, "code")?;
        let locale = field(&row, "locale")?;
        // Python constructs the two-field tuple before testing membership.
        let code = python_string_bytes(&code)?;
        let locale = python_string_bytes(&locale)?;
        if key == code || key == locale {
            return Some((Some(field(&row, "name")?),));
        }
    }
    // A successful miss is distinct from an unsupported input.
    Some((None,))
}

#[pyfunction]
pub(crate) fn project_catalog_language_codes<'py>(
    rows: &Bound<'py, PyAny>,
    always_visible: &Bound<'_, PyAny>,
    min_percent: &Bound<'_, PyAny>,
) -> Option<Vec<Bound<'py, PyString>>> {
    let always_visible = python_string_bytes(always_visible.cast_exact::<PyString>().ok()?)?;
    let min_percent = min_percent
        .cast_exact::<PyInt>()
        .ok()?
        .extract::<i64>()
        .ok()?;
    let mut result = Vec::new();
    for row in rows.cast_exact::<PyList>().ok()?.iter() {
        let row = plain_row(&row)?;
        let code = field(&row, "code")?;
        if python_string_bytes(&code)? == always_visible
            || row
                .get_item("percent_translated")
                .ok()??
                .cast_exact::<PyInt>()
                .ok()?
                .extract::<i64>()
                .ok()?
                >= min_percent
        {
            result.push(code);
        }
    }
    Some(result)
}
