//! Presentation-only joining of previously constructed silent mention strings.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyList, PyString};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

type MentionParts<'py> = (Option<Bound<'py, PyString>>, Bound<'py, PyString>);

#[pyfunction]
pub(crate) fn plan_display_mentions<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    sort: &Bound<'_, PyAny>,
) -> Option<MentionParts<'py>> {
    let sort = sort.cast_exact::<PyBool>().ok()?.is_true();
    let values = value.cast_exact::<PyList>().ok()?;
    let strings = values
        .iter()
        .map(|item| item.cast_into_exact::<PyString>().ok())
        .collect::<Option<Vec<_>>>()?;
    // Surrogate-pass UTF-8 preserves Python's code-point order and lets
    // us join exact strings without changing isolated surrogate values.
    let mut ordered = strings
        .iter()
        .map(|value| Some((python_string_bytes(value)?, value)))
        .collect::<Option<Vec<_>>>()?;
    if sort {
        ordered.sort_by(|(left, _), (right, _)| left.cmp(right));
    }
    let ((_, last), preceding) = ordered.split_last()?;
    if preceding.is_empty() {
        // Preserve the original single Python string, not a reconversion.
        return Some((None, (*last).clone()));
    }
    let mut head = Vec::new();
    for (index, (item, _)) in preceding.iter().enumerate() {
        if index != 0 {
            head.extend_from_slice(b", ");
        }
        head.extend_from_slice(item);
    }
    if preceding.len() > 1 {
        head.push(b',');
    }
    let text = python_string_from_bytes(py, &head)?;
    Some((Some(text), (*last).clone()))
}
