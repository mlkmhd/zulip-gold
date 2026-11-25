//! Display-only cosmetics for prepared migration diagnostic values.

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};
use pyo3::prelude::*;
use pyo3::types::{PyInt, PyString};

#[pyfunction]
pub(crate) fn strip_migration_display_sgr<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let value = value.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(value)?;
    let mut last = 0;
    let mut output = Vec::new();
    for (start, _) in text
        .windows(2)
        .enumerate()
        .filter(|(_, window)| *window == b"\x1b[")
    {
        let mut end = start + 2;
        while text
            .get(end)
            .is_some_and(|b| b.is_ascii_digit() || *b == b';')
        {
            end += 1;
        }
        if text.get(end) == Some(&b'm') {
            output.extend_from_slice(&text[last..start]);
            last = end + 1;
        }
    }
    if last == 0 {
        return Some(value.clone());
    }
    output.extend_from_slice(&text[last..]);
    python_string_from_bytes(py, &output)
}

#[pyfunction]
pub(crate) fn format_migration_display_row<'py>(
    badge: &Bound<'py, PyAny>,
    title: &Bound<'py, PyAny>,
    squashed_count: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let badge = badge.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let title = title.cast_exact::<PyString>().ok()?;
    if !matches!(badge, "X" | "-" | " ") {
        return None;
    }
    let count = squashed_count
        .cast_exact::<PyInt>()
        .ok()?
        .extract::<u64>()
        .ok()?;
    let text = python_string_bytes(title)?;
    let mut output = Vec::with_capacity(text.len().checked_add(4)?);
    output.extend_from_slice(&[b'[', *badge.as_bytes().first()?, b']', b' ']);
    output.extend_from_slice(&text);
    if count != 0 {
        output.extend_from_slice(format!(" ({count} squashed migrations)").as_bytes());
    }
    python_string_from_bytes(title.py(), &output)
}
