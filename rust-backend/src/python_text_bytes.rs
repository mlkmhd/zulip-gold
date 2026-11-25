//! Surrogate-pass UTF-8 for operations on Python strings and ASCII delimiters.

use std::borrow::Cow;

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyString};

pub(crate) fn python_string_bytes<'a>(value: &'a Bound<'_, PyString>) -> Option<Cow<'a, [u8]>> {
    if let Ok(text) = value.to_str() {
        return Some(Cow::Borrowed(text.as_bytes()));
    }
    let encoded = value
        .py()
        .get_type::<PyString>()
        .call_method1("encode", (value, "utf-8", "surrogatepass"))
        .ok()?
        .cast_into_exact::<PyBytes>()
        .ok()?;
    Some(Cow::Owned(encoded.as_bytes().to_vec()))
}

pub(crate) fn python_string_from_bytes<'py>(
    py: Python<'py>,
    bytes: &[u8],
) -> Option<Bound<'py, PyString>> {
    if let Ok(text) = std::str::from_utf8(bytes) {
        return Some(PyString::new(py, text));
    }
    PyBytes::new(py, bytes)
        .call_method1("decode", ("utf-8", "surrogatepass"))
        .ok()?
        .cast_into_exact::<PyString>()
        .ok()
}

pub(crate) fn join_exact_python_strings<'py>(
    parts: &Bound<'py, PyAny>,
    delimiter: &[u8],
) -> Option<Bound<'py, PyString>> {
    let parts = parts.cast_exact::<PyList>().ok()?;
    if parts.len() == 1 {
        let value = parts.get_item(0).ok()?;
        return Some(value.cast_exact::<PyString>().ok()?.clone());
    }
    let mut output = Vec::new();
    for (index, value) in parts.iter().enumerate() {
        let value = value.cast_exact::<PyString>().ok()?;
        if index != 0 {
            output.extend_from_slice(delimiter);
        }
        output.extend_from_slice(&python_string_bytes(value)?);
    }
    python_string_from_bytes(parts.py(), &output)
}

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

pub(crate) fn trim_python_text(text: &[u8]) -> &[u8] {
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
