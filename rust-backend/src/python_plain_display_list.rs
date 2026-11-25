//! List joining for already selected plain-text display only.

use std::io::Write;

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyString};

#[pyfunction]
pub(crate) fn format_plain_display_list<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyAny>,
    limit: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let limit = limit.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let values = values.cast_exact::<PyList>().ok()?;
    // Never treat a str subclass as plain text; markup and custom behavior
    // belong entirely to the existing Python path.
    let strings = values
        .iter()
        .map(|value| value.cast_into_exact::<PyString>().ok())
        .collect::<Option<Vec<_>>>()?;
    if strings.len() == 1 {
        return strings.into_iter().next();
    }

    let length = i64::try_from(strings.len()).ok()?;
    let mut output = Vec::new();
    let visible_end = if length <= limit {
        // Preserve the original empty-list IndexError via fallback.
        length.checked_sub(1)?
    } else if limit < 0 {
        (length + limit).max(0)
    } else {
        limit
    };
    for (index, value) in strings[..usize::try_from(visible_end).ok()?]
        .iter()
        .enumerate()
    {
        if index != 0 {
            output.extend_from_slice(b", ");
        }
        output.extend_from_slice(&python_string_bytes(value)?);
    }
    if length <= limit {
        output.extend_from_slice(b" and ");
        output.extend_from_slice(&python_string_bytes(strings.last()?)?);
    } else {
        let hidden = length.checked_sub(limit)?;
        let plural = if hidden == 1 { "" } else { "s" };
        write!(&mut output, " and {hidden} other{plural}").ok()?;
    }
    python_string_from_bytes(py, &output)
}
