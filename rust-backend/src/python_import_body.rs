//! Normalize already decoded message bodies without touching import records.

use std::borrow::Cow;

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyString};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

const TRUNCATION: &[u8] = b"\n[message truncated]";

fn normalize_import_bytes(body: &[u8], max_length: i64) -> Cow<'_, [u8]> {
    let cleaned = if body.contains(&0) {
        Cow::Owned(body.iter().copied().filter(|&byte| byte != 0).collect())
    } else {
        Cow::Borrowed(body)
    };
    // UTF-8 and surrogate-pass both use exactly one leading byte for
    // each Python code point. Removing NUL never splits an encoding.
    let is_lead = |byte: u8| byte & 0xc0 != 0x80;
    let length = cleaned.iter().filter(|&&byte| is_lead(byte)).count() as i128;
    let max_length = i128::from(max_length);
    if length <= max_length {
        return cleaned;
    }

    // Match Python's negative slice stop when the suffix exceeds the limit.
    let stop = max_length - TRUNCATION.len() as i128;
    let end = (if stop < 0 { length + stop } else { stop }).clamp(0, length) as usize;
    let byte = cleaned
        .iter()
        .enumerate()
        .filter(|(_, byte)| is_lead(**byte))
        .nth(end)
        .map_or(cleaned.len(), |(index, _)| index);
    let mut result = Vec::with_capacity(byte + TRUNCATION.len());
    result.extend_from_slice(&cleaned[..byte]);
    result.extend_from_slice(TRUNCATION);
    Cow::Owned(result)
}

fn import_limit(value: &Bound<'_, PyAny>) -> Option<i64> {
    value.cast_exact::<PyInt>().ok()?.extract().ok()
}

fn import_output<'py>(
    body: &Bound<'py, PyString>,
    bytes: &[u8],
    max_length: i64,
) -> Option<Bound<'py, PyString>> {
    match normalize_import_bytes(bytes, max_length) {
        Cow::Borrowed(_) => Some(body.clone()),
        Cow::Owned(result) => python_string_from_bytes(body.py(), &result),
    }
}

#[pyfunction]
pub(crate) fn normalize_imported_message_body<'py>(
    body: &Bound<'py, PyAny>,
    max_length: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let body = body.cast_exact::<PyString>().ok()?;
    let max_length = import_limit(max_length)?;
    import_output(body, body.to_str().ok()?.as_bytes(), max_length)
}

#[pyfunction]
pub(crate) fn normalize_exact_imported_message_body<'py>(
    body: &Bound<'py, PyAny>,
    max_length: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let body = body.cast_exact::<PyString>().ok()?;
    let max_length = import_limit(max_length)?;
    let bytes = python_string_bytes(body)?;
    import_output(body, &bytes, max_length)
}
