//! List joining for already selected plain-text display only.

use std::fmt::Write;

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
    let refs = strings
        .iter()
        .map(|value| value.to_str().ok())
        .collect::<Option<Vec<_>>>()?;
    if refs.len() == 1 {
        return strings.into_iter().next();
    }

    let length = i64::try_from(refs.len()).ok()?;
    let output = if length <= limit {
        // Preserve the original empty-list IndexError via fallback.
        let (last, previous) = refs.split_last()?;
        let mut output = previous.join(", ");
        output.push_str(" and ");
        output.push_str(last);
        output
    } else {
        let end = if limit < 0 {
            (length + limit).max(0)
        } else {
            limit
        };
        let hidden = length.checked_sub(limit)?;
        let mut output = refs[..usize::try_from(end).ok()?].join(", ");
        let plural = if hidden == 1 { "" } else { "s" };
        write!(&mut output, " and {hidden} other{plural}").ok()?;
        output
    };
    Some(PyString::new(py, &output))
}
