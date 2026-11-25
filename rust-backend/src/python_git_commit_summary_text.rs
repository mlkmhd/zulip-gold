//! Finish prepared display text without inspecting its rows.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyString};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

fn py_space(ch: char) -> bool {
    matches!(
        ch,
        '\u{9}'..='\u{d}' | '\u{1c}'..='\u{20}' | '\u{85}' | '\u{a0}' | '\u{1680}'
            | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}'
            | '\u{3000}'
    )
}

fn trim_python_whitespace(bytes: &[u8]) -> &[u8] {
    if let Ok(text) = std::str::from_utf8(bytes) {
        return text.trim_end_matches(py_space).as_bytes();
    }

    let mut end = bytes.len();
    while end > 0 {
        let mut start = end - 1;
        while start > 0 && bytes[start] & 0xc0 == 0x80 {
            start -= 1;
        }
        // Surrogate-pass bytes have valid code point boundaries. A single
        // UTF-8 decoding failure at this boundary is a surrogate, not space.
        if !std::str::from_utf8(&bytes[start..end])
            .ok()
            .and_then(|point| point.chars().next())
            .is_some_and(py_space)
        {
            break;
        }
        end = start;
    }
    &bytes[..end]
}

#[pyfunction]
pub(crate) fn finish_git_commit_summary_text<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
    omission: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let rows = rows.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(rows)?;
    if omission.is_none() {
        let trimmed = trim_python_whitespace(&text);
        return if trimmed.len() == text.len() {
            Some(rows.clone())
        } else {
            python_string_from_bytes(py, trimmed)
        };
    }

    let count = omission.cast_exact::<PyInt>().ok()?.extract::<u64>().ok()?;
    let suffix = if count == 0 {
        "[and more commit(s)]".to_owned()
    } else {
        format!("[and {count} more commit(s)]")
    };
    let mut result = text.into_owned();
    result.extend_from_slice(suffix.as_bytes());
    python_string_from_bytes(py, &result)
}
