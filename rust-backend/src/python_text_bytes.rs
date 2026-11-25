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
