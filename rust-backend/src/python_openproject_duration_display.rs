//! Display wording over already parsed OpenProject duration parts.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyString};

#[pyfunction]
pub(crate) fn format_openproject_duration_part(
    amount: &Bound<'_, PyAny>,
    label: &Bound<'_, PyAny>,
) -> Option<String> {
    let amount = amount.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let label = label.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("{amount} {label}"))
}

#[pyfunction]
pub(crate) fn join_openproject_duration_parts(parts: &Bound<'_, PyAny>) -> Option<String> {
    let parts = parts.cast_exact::<PyList>().ok()?;
    if parts.len() < 2 {
        return None;
    }

    let last = parts.len() - 1;
    let mut output = String::new();
    for (index, part) in parts.iter().enumerate() {
        if index == last {
            output.push_str(" and ");
        } else if index != 0 {
            output.push_str(", ");
        }
        output.push_str(part.cast_exact::<PyString>().ok()?.to_str().ok()?);
    }
    Some(output)
}
