//! Presentation-only joining of previously constructed silent mention strings.

use std::borrow::Cow;

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyList, PyString};

type MentionParts<'py> = (Option<Bound<'py, PyString>>, Bound<'py, PyString>);

fn ordered_str_bytes<'a>(value: &'a Bound<'_, PyString>) -> Option<Cow<'a, [u8]>> {
    if let Ok(text) = value.to_str() {
        return Some(Cow::Borrowed(text.as_bytes()));
    }
    let encoded = value
        .call_method1("encode", ("utf-8", "surrogatepass"))
        .ok()?
        .cast_into_exact::<PyBytes>()
        .ok()?;
    Some(Cow::Owned(encoded.as_bytes().to_vec()))
}

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
        .map(|value| Some((ordered_str_bytes(value)?, value)))
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
    let text = if let Ok(text) = std::str::from_utf8(&head) {
        PyString::new(py, text)
    } else {
        PyBytes::new(py, &head)
            .call_method1("decode", ("utf-8", "surrogatepass"))
            .ok()?
            .cast_into_exact::<PyString>()
            .ok()?
    };
    Some((Some(text), (*last).clone()))
}
