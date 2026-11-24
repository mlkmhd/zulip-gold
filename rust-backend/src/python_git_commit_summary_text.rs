//! Finish prepared display text without inspecting its rows.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyString};

fn py_space(ch: char) -> bool {
    matches!(
        ch,
        '\u{9}'..='\u{d}' | '\u{1c}'..='\u{20}' | '\u{85}' | '\u{a0}' | '\u{1680}'
            | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}'
            | '\u{3000}'
    )
}

#[pyfunction]
pub(crate) fn finish_git_commit_summary_text<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
    omission: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let rows = rows.cast_exact::<PyString>().ok()?;
    let text = rows.to_str().ok()?;
    if omission.is_none() {
        let trimmed = text.trim_end_matches(py_space);
        return Some(if trimmed.len() == text.len() {
            rows.clone()
        } else {
            PyString::new(py, trimmed)
        });
    }

    let count = omission.cast_exact::<PyInt>().ok()?.extract::<u64>().ok()?;
    // No trailing whitespace is introduced by either literal suffix.
    let result = if count == 0 {
        format!("{text}[and more commit(s)]")
    } else {
        format!("{text}[and {count} more commit(s)]")
    };
    Some(PyString::new(py, &result))
}
