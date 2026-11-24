//! Normalize already decoded message bodies without touching import records.

use std::borrow::Cow;

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyString};

const TRUNCATION: &str = "\n[message truncated]";

#[pyfunction]
pub(crate) fn normalize_imported_message_body<'py>(
    body: &Bound<'py, PyAny>,
    max_length: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let body = body.cast_exact::<PyString>().ok()?;
    let max_length = max_length
        .cast_exact::<PyInt>()
        .ok()?
        .extract::<i64>()
        .ok()?;
    let text = body.to_str().ok()?;
    let cleaned = if text.contains('\0') {
        Cow::Owned(text.replace('\0', ""))
    } else {
        Cow::Borrowed(text)
    };
    let length = cleaned.chars().count() as i128;
    let max_length = i128::from(max_length);
    if length <= max_length {
        return Some(match cleaned {
            Cow::Borrowed(_) => body.clone(),
            Cow::Owned(cleaned) => PyString::new(body.py(), &cleaned),
        });
    }

    // Match Python's stop-only slice, including a stop before zero when the
    // configured limit is shorter than the ASCII truncation suffix.
    let stop = max_length - TRUNCATION.len() as i128;
    let end = (if stop < 0 { length + stop } else { stop }).clamp(0, length) as usize;
    let byte = cleaned
        .char_indices()
        .nth(end)
        .map_or(cleaned.len(), |(byte, _)| byte);
    let mut result = String::with_capacity(byte + TRUNCATION.len());
    result.push_str(&cleaned[..byte]);
    result.push_str(TRUNCATION);
    Some(PyString::new(body.py(), &result))
}
