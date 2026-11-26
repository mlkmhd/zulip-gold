//! Framework-independent transforms of already selected Unicode emoji strings.

use std::fmt::Write as _;

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyString};

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

fn format_emoji_points(points: impl Iterator<Item = u32>, capacity: usize) -> Option<String> {
    let mut encoded = String::new();
    encoded.try_reserve(capacity).ok()?;
    for (index, value) in points.enumerate() {
        if index != 0 {
            encoded.push('-');
        }
        write!(&mut encoded, "{value:04x}").ok()?;
    }
    Some(encoded)
}

#[pyfunction]
pub(crate) fn encode_emoji_codepoints(input: &Bound<'_, PyAny>) -> Option<String> {
    let text = input.cast_exact::<PyString>().ok()?.to_str().ok()?;
    format_emoji_points(text.chars().map(u32::from), text.len())
}

#[pyfunction]
pub(crate) fn encode_exact_emoji_codepoints(input: &Bound<'_, PyAny>) -> Option<String> {
    let input = input.cast_exact::<PyString>().ok()?;
    if let Ok(text) = input.to_str() {
        return format_emoji_points(text.chars().map(u32::from), text.len());
    }
    // UTF-32 with surrogate-pass supplies the same code points as Python ord.
    let encoded = input
        .call_method1("encode", ("utf-32-le", "surrogatepass"))
        .ok()?
        .cast_into_exact::<PyBytes>()
        .ok()?;
    let bytes = encoded.as_bytes();
    let points = bytes
        .chunks_exact(4)
        .map(|part| u32::from_le_bytes([part[0], part[1], part[2], part[3]]));
    format_emoji_points(points, bytes.len())
}

fn parse_ascii_emoji_point(part: &str) -> Option<u32> {
    // Whitespace, underscores, signs, prefixes and non-ASCII digits
    // retain the original Python int spelling rules and errors.
    if !part.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return None;
    }
    u32::from_str_radix(part, 16).ok()
}

#[pyfunction]
pub(crate) fn decode_emoji_codepoints(input: &Bound<'_, PyAny>) -> Option<String> {
    let text = input.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let mut decoded = String::with_capacity(text.len());
    for part in text.split('-') {
        decoded.push(char::from_u32(parse_ascii_emoji_point(part)?)?);
    }
    Some(decoded)
}

#[pyfunction]
pub(crate) fn decode_exact_emoji_codepoints<'py>(
    input: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let input = input.cast_exact::<PyString>().ok()?;
    let text = input.to_str().ok()?;
    let mut decoded = Vec::new();
    decoded.try_reserve(text.len()).ok()?;
    for part in text.split('-') {
        let code = parse_ascii_emoji_point(part)?;
        if let Some(ch) = char::from_u32(code) {
            let mut utf8 = [0; 4];
            decoded.extend_from_slice(ch.encode_utf8(&mut utf8).as_bytes());
        } else if (0xd800..=0xdfff).contains(&code) {
            // Encode a lone surrogate using the bytes accepted by
            // Python's surrogate-pass codec, never as a Rust char.
            decoded.extend_from_slice(&[
                (0xe0 | (code >> 12)) as u8,
                (0x80 | ((code >> 6) & 0x3f)) as u8,
                (0x80 | (code & 0x3f)) as u8,
            ]);
        } else {
            return None;
        }
    }
    python_string_from_bytes(input.py(), &decoded)
}
