//! Presentation-only joining of previously constructed silent mention strings.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyList, PyString};

type MentionParts<'py> = (Option<String>, Bound<'py, PyString>);

#[pyfunction]
pub(crate) fn plan_display_mentions<'py>(
    value: &Bound<'py, PyAny>,
    sort: &Bound<'_, PyAny>,
) -> Option<MentionParts<'py>> {
    let sort = sort.cast_exact::<PyBool>().ok()?.is_true();
    let values = value.cast_exact::<PyList>().ok()?;
    let strings = values
        .iter()
        .map(|item| item.cast_into_exact::<PyString>().ok())
        .collect::<Option<Vec<_>>>()?;
    // UTF-8 lexical order equals Python code-point order for scalar-only
    // strings. Leave lone surrogates and non-exact strings to Python.
    let mut ordered = strings
        .iter()
        .map(|value| Some((value.to_str().ok()?, value)))
        .collect::<Option<Vec<_>>>()?;
    if sort {
        ordered.sort_by(|(left, _), (right, _)| left.cmp(right));
    }
    let ((_, last), preceding) = ordered.split_last()?;
    if preceding.is_empty() {
        // Preserve the original single Python string, not a reconversion.
        return Some((None, (*last).clone()));
    }
    let mut head = String::new();
    for (index, (item, _)) in preceding.iter().enumerate() {
        if index != 0 {
            head.push_str(", ");
        }
        head.push_str(item);
    }
    if preceding.len() > 1 {
        head.push(',');
    }
    Some((Some(head), (*last).clone()))
}
