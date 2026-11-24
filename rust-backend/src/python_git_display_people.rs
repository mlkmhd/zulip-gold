//! Legacy final punctuation over already selected display strings.

use std::fmt::Write;

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyString, PyTuple};

#[pyfunction]
pub(crate) fn join_git_display_assignees<'py>(
    py: Python<'py>,
    names: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let names = names.cast_exact::<PyList>().ok()?;
    let values = names
        .iter()
        .map(|name| name.cast_into_exact::<PyString>().ok())
        .collect::<Option<Vec<_>>>()?;
    let strings = values
        .iter()
        .map(|name| name.to_str().ok())
        .collect::<Option<Vec<_>>>()?;
    let (last, prior) = strings.split_last()?;
    if prior.is_empty() {
        return values.into_iter().next();
    }
    let mut output = prior.join(", ");
    output.push_str(" and ");
    output.push_str(last);
    Some(PyString::new(py, &output))
}

#[pyfunction]
pub(crate) fn format_git_display_committers(rows: &Bound<'_, PyAny>) -> Option<String> {
    let rows = rows.cast_exact::<PyList>().ok()?;
    if rows.is_empty() {
        return None;
    }
    let mut output = String::from("Commits by ");
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
            output.push_str(" and ");
        } else if index != 0 {
            output.push_str(", ");
        }
        write!(&mut output, "{} ({count})", name.to_str().ok()?).ok()?;
    }
    Some(output)
}
