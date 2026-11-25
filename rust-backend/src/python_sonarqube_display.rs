//! Literal formatting for already prepared SonarQube display text.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

#[pyfunction]
pub(crate) fn format_sonarqube_metric_label<'py>(
    py: Python<'py>,
    label: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let label = label.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(label)?;
    if !text.contains(&b'_') {
        return Some(label.clone());
    }
    let mut rewritten = text.into_owned();
    for byte in &mut rewritten {
        if *byte == b'_' {
            *byte = b' ';
        }
    }
    python_string_from_bytes(py, &rewritten)
}

#[pyfunction]
pub(crate) fn join_sonarqube_condition_lines<'py>(
    py: Python<'py>,
    lines: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let lines = lines.cast_exact::<PyList>().ok()?;
    if lines.len() == 1 {
        return lines.get_item(0).ok()?.cast_into_exact::<PyString>().ok();
    }

    let mut text = Vec::new();
    for (index, item) in lines.iter().enumerate() {
        if index != 0 {
            text.push(b'\n');
        }
        text.extend_from_slice(&python_string_bytes(item.cast_exact::<PyString>().ok()?)?);
    }
    python_string_from_bytes(py, &text)
}
