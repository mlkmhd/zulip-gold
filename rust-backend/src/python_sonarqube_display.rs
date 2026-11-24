//! Literal formatting for already prepared SonarQube display text.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

#[pyfunction]
pub(crate) fn format_sonarqube_metric_label<'py>(
    py: Python<'py>,
    label: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let label = label.cast_exact::<PyString>().ok()?;
    let text = label.to_str().ok()?;
    if !text.contains('_') {
        return Some(label.clone());
    }
    Some(PyString::new(py, &text.replace('_', " ")))
}

#[pyfunction]
pub(crate) fn join_sonarqube_condition_lines<'py>(
    py: Python<'py>,
    lines: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let lines = lines.cast_exact::<PyList>().ok()?;
    if lines.len() == 1 {
        let line = lines.get_item(0).ok()?.cast_into_exact::<PyString>().ok()?;
        line.to_str().ok()?;
        return Some(line);
    }

    let mut text = String::new();
    for (index, item) in lines.iter().enumerate() {
        if index != 0 {
            text.push('\n');
        }
        text.push_str(item.cast_exact::<PyString>().ok()?.to_str().ok()?);
    }
    Some(PyString::new(py, &text))
}
