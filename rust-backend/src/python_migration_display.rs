//! Display-only cosmetics for prepared migration diagnostic values.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyString};

#[pyfunction]
pub(crate) fn strip_migration_display_sgr<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let value = value.cast_exact::<PyString>().ok()?;
    let text = value.to_str().ok()?;
    let mut last = 0;
    let mut output = String::new();
    for (start, _) in text.match_indices("\x1b[") {
        let mut end = start + 2;
        let bytes = text.as_bytes();
        while bytes
            .get(end)
            .is_some_and(|b| b.is_ascii_digit() || *b == b';')
        {
            end += 1;
        }
        if bytes.get(end) == Some(&b'm') {
            output.push_str(&text[last..start]);
            last = end + 1;
        }
    }
    if last == 0 {
        return Some(value.clone());
    }
    output.push_str(&text[last..]);
    Some(PyString::new(py, &output))
}

#[pyfunction]
pub(crate) fn format_migration_display_row(
    badge: &Bound<'_, PyAny>,
    title: &Bound<'_, PyAny>,
    squashed_count: &Bound<'_, PyAny>,
) -> Option<String> {
    let badge = badge.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let title = title.cast_exact::<PyString>().ok()?.to_str().ok()?;
    if !matches!(badge, "X" | "-" | " ") {
        return None;
    }
    let count = squashed_count
        .cast_exact::<PyInt>()
        .ok()?
        .extract::<u64>()
        .ok()?;
    if count == 0 {
        Some(format!("[{badge}] {title}"))
    } else {
        Some(format!("[{badge}] {title} ({count} squashed migrations)"))
    }
}
