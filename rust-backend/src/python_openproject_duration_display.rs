//! Display wording over already parsed OpenProject duration parts.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyString};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

#[pyfunction]
pub(crate) fn format_openproject_duration_part<'py>(
    amount: &Bound<'_, PyAny>,
    label: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let amount = amount.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let text = python_string_bytes(label.cast_exact::<PyString>().ok()?)?;
    let mut output = format!("{amount} ").into_bytes();
    output.extend_from_slice(&text);
    python_string_from_bytes(label.py(), &output)
}

#[pyfunction]
pub(crate) fn join_openproject_duration_parts<'py>(
    parts: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let parts = parts.cast_exact::<PyList>().ok()?;
    if parts.len() < 2 {
        return None;
    }

    let last = parts.len() - 1;
    let mut output = Vec::new();
    for (index, part) in parts.iter().enumerate() {
        if index == last {
            output.extend_from_slice(b" and ");
        } else if index != 0 {
            output.extend_from_slice(b", ");
        }
        output.extend_from_slice(&python_string_bytes(part.cast_exact::<PyString>().ok()?)?);
    }
    python_string_from_bytes(parts.py(), &output)
}
