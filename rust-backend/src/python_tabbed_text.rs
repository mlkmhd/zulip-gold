//! Fixed text transformations over Python-selected documentation tab content.

use crate::python_text_bytes::{
    join_exact_python_strings, python_string_bytes, python_string_from_bytes,
};
use pyo3::prelude::*;
use pyo3::types::PyString;

fn is_python_space(c: char) -> bool {
    // CPython str.strip(): Unicode spacing plus the ASCII information separators.
    matches!(
        c,
        '\t'..='\r'
            | '\u{001c}'..=' '
            | '\u{0085}'
            | '\u{00a0}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200a}'
            | '\u{2028}'
            | '\u{2029}'
            | '\u{202f}'
            | '\u{205f}'
            | '\u{3000}'
    )
}

fn trim_python_text(text: &[u8]) -> &[u8] {
    if let Ok(text) = std::str::from_utf8(text) {
        return text.trim_matches(is_python_space).as_bytes();
    }

    let mut first = None;
    let mut last = 0;
    let mut offset = 0;
    for chunk in text.utf8_chunks() {
        for (i, ch) in chunk.valid().char_indices() {
            if !is_python_space(ch) {
                first.get_or_insert(offset + i);
                last = offset + i + ch.len_utf8();
            }
        }
        offset += chunk.valid().len();
        // Lone surrogate bytes cannot be Python whitespace.
        if !chunk.invalid().is_empty() {
            first.get_or_insert(offset);
            offset += chunk.invalid().len();
            last = offset;
        }
    }
    &text[first.unwrap_or(0)..last]
}

#[pyfunction]
pub(crate) fn normalize_prepared_tabbed_lines<'py>(
    lines: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let joined = join_exact_python_strings(lines, b"\n")?;
    let text = python_string_bytes(&joined)?;
    let trimmed = trim_python_text(&text);
    if trimmed.len() == text.len() {
        return Some(joined);
    }
    python_string_from_bytes(joined.py(), trimmed)
}

#[pyfunction]
pub(crate) fn join_prepared_tabbed_blocks<'py>(
    tabs: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    join_exact_python_strings(tabs, b"\n")
}
