//! Comma-joining of already-formatted API documentation examples.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

#[pyfunction]
pub(crate) fn join_prepared_api_enum_examples<'py>(
    parts: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let parts = parts.cast_exact::<PyList>().ok()?;
    if parts.len() == 1 {
        let value = parts.get_item(0).ok()?;
        return Some(value.cast_exact::<PyString>().ok()?.clone());
    }
    let mut output = String::new();
    for (index, value) in parts.iter().enumerate() {
        let value = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
        if index != 0 {
            output.push_str(", ");
        }
        output.push_str(value);
    }
    Some(PyString::new(parts.py(), &output))
}
