//! Literal Redmine display text over already selected fields.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyString};

#[pyfunction]
pub(crate) fn format_redmine_display_issue_label(
    number: &Bound<'_, PyAny>,
    title: &Bound<'_, PyAny>,
) -> Option<String> {
    let number = number.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let title = title.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("#{number} {title}"))
}

#[pyfunction]
pub(crate) fn join_redmine_display_name(
    first_name: &Bound<'_, PyAny>,
    last_name: &Bound<'_, PyAny>,
) -> Option<String> {
    let first_name = first_name.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let last_name = last_name.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("{first_name} {last_name}"))
}
