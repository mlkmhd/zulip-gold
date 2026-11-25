//! LF indentation of already-selected documentation text.

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};
use pyo3::prelude::*;
use pyo3::types::PyString;

#[pyfunction]
pub(crate) fn indent_prepared_api_return_description<'py>(
    description: &Bound<'py, PyAny>,
    replacement: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let description = description.cast_exact::<PyString>().ok()?;
    let replacement = replacement.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let indent = replacement.strip_prefix('\n')?;
    if !indent.bytes().all(|byte| byte == b' ') {
        return None;
    }
    if indent.is_empty() {
        return Some(description.clone());
    }
    let text = python_string_bytes(description)?;
    if !text.contains(&b'\n') {
        return Some(description.clone());
    }

    let newline_count = text.iter().filter(|&&byte| byte == b'\n').count();
    let output_bytes = text
        .len()
        .checked_add(newline_count.checked_mul(indent.len())?)?;
    // Leave unusually large copies to the original Python implementation.
    if output_bytes > 8 * 1024 * 1024 {
        return None;
    }
    let mut output = Vec::with_capacity(output_bytes);
    for (index, segment) in text.split(|&byte| byte == b'\n').enumerate() {
        if index != 0 {
            output.extend_from_slice(replacement.as_bytes());
        }
        output.extend_from_slice(segment);
    }
    python_string_from_bytes(description.py(), &output)
}
