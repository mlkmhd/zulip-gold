//! Literal Redmine display text over already selected fields.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyString};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

#[pyfunction]
pub(crate) fn format_redmine_display_issue_label<'py>(
    number: &Bound<'_, PyAny>,
    title: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let number = number.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let raw = python_string_bytes(title.cast_exact::<PyString>().ok()?)?;
    let mut output = format!("#{number} ").into_bytes();
    output.extend_from_slice(&raw);
    python_string_from_bytes(title.py(), &output)
}

#[pyfunction]
pub(crate) fn join_redmine_display_name<'py>(
    first_name: &Bound<'py, PyAny>,
    last_name: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let first = python_string_bytes(first_name.cast_exact::<PyString>().ok()?)?;
    let last = python_string_bytes(last_name.cast_exact::<PyString>().ok()?)?;
    let mut output = Vec::with_capacity(first.len() + 1 + last.len());
    output.extend_from_slice(&first);
    output.push(b' ');
    output.extend_from_slice(&last);
    python_string_from_bytes(first_name.py(), &output)
}
