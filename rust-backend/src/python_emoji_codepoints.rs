//! Framework-independent transforms of already selected Unicode emoji strings.

use std::fmt::Write as _;

use pyo3::prelude::*;
use pyo3::types::PyString;

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

#[pyfunction]
pub(crate) fn unqualify_emoji_text<'py>(input: &Bound<'py, PyAny>) -> Option<Bound<'py, PyString>> {
    let input = input.cast_exact::<PyString>().ok()?;
    let text = input.to_str().ok()?;
    if !text.contains('\u{fe0f}') {
        return Some(input.clone());
    }
    Some(PyString::new(input.py(), &text.replace('\u{fe0f}', "")))
}

#[pyfunction]
pub(crate) fn unqualify_exact_emoji_text<'py>(
    input: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let input = input.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(input)?;
    // UTF-8 (including surrogate-pass) cannot embed these bytes in
    // the encoding of another code point.
    let selector = "\u{fe0f}".as_bytes();
    let mut positions = text
        .windows(selector.len())
        .enumerate()
        .filter_map(|(index, candidate)| (candidate == selector).then_some(index));
    let Some(first) = positions.next() else {
        return Some(input.clone());
    };
    let mut result = Vec::new();
    result.try_reserve_exact(text.len() - selector.len()).ok()?;
    result.extend_from_slice(&text[..first]);
    let mut start = first + selector.len();
    for position in positions {
        result.extend_from_slice(&text[start..position]);
        start = position + selector.len();
    }
    result.extend_from_slice(&text[start..]);
    python_string_from_bytes(input.py(), &result)
}

#[pyfunction]
pub(crate) fn encode_emoji_codepoints(input: &Bound<'_, PyAny>) -> Option<String> {
    let text = input.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let mut encoded = String::with_capacity(text.len());
    for (index, value) in text.chars().enumerate() {
        if index != 0 {
            encoded.push('-');
        }
        write!(&mut encoded, "{:04x}", u32::from(value)).ok()?;
    }
    Some(encoded)
}

#[pyfunction]
pub(crate) fn decode_emoji_codepoints(input: &Bound<'_, PyAny>) -> Option<String> {
    let text = input.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let mut decoded = String::with_capacity(text.len());
    for part in text.split('-') {
        // Leave Python's permissive integer spellings, error types and
        // Unicode surrogate output to the original reference.
        if !part.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return None;
        }
        let value = u32::from_str_radix(part, 16).ok()?;
        decoded.push(char::from_u32(value)?);
    }
    Some(decoded)
}
