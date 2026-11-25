//! Legacy final punctuation over already selected display strings.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyString, PyTuple};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

#[pyfunction]
pub(crate) fn join_git_display_assignees<'py>(
    py: Python<'py>,
    names: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let names = names.cast_exact::<PyList>().ok()?;
    if names.len() == 1 {
        return names.get_item(0).ok()?.cast_into_exact().ok();
    }
    if names.is_empty() {
        return None;
    }
    let mut output = Vec::new();
    for (index, name) in names.iter().enumerate() {
        if index > 0 {
            let separator: &[u8] = if index == names.len() - 1 {
                b" and "
            } else {
                b", "
            };
            output.extend_from_slice(separator);
        }
        output.extend_from_slice(&python_string_bytes(name.cast_exact::<PyString>().ok()?)?);
    }
    python_string_from_bytes(py, &output)
}

#[pyfunction]
pub(crate) fn format_git_display_committers<'py>(
    rows: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let rows = rows.cast_exact::<PyList>().ok()?;
    if rows.is_empty() {
        return None;
    }
    let mut output = b"Commits by ".to_vec();
    for (index, item) in rows.iter().enumerate() {
        let row = item.cast_exact::<PyTuple>().ok()?;
        if row.len() != 2 {
            return None;
        }
        let name = row.get_item(0).ok()?.cast_into_exact::<PyString>().ok()?;
        let count = row
            .get_item(1)
            .ok()?
            .cast_into_exact::<PyInt>()
            .ok()?
            .extract::<i64>()
            .ok()?;
        if index == rows.len() - 1 && index != 0 {
            output.extend_from_slice(b" and ");
        } else if index != 0 {
            output.extend_from_slice(b", ");
        }
        output.extend_from_slice(&python_string_bytes(&name)?);
        output.extend_from_slice(format!(" ({count})").as_bytes());
    }
    python_string_from_bytes(rows.py(), &output)
}
