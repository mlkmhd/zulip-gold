//! LF indentation of already-selected documentation text.

use pyo3::prelude::*;
use pyo3::types::PyString;

#[pyfunction]
pub(crate) fn indent_prepared_api_return_description<'py>(
    description: &Bound<'py, PyAny>,
    replacement: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let description = description.cast_exact::<PyString>().ok()?;
    let text = description.to_str().ok()?;
    let replacement = replacement.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let indent = replacement.strip_prefix('\n')?;
    if !indent.bytes().all(|byte| byte == b' ') {
        return None;
    }
    if indent.is_empty() || !text.contains('\n') {
        return Some(description.clone());
    }

    let newline_count = text.bytes().filter(|&byte| byte == b'\n').count();
    let output_bytes = text
        .len()
        .checked_add(newline_count.checked_mul(indent.len())?)?;
    // Leave unusually large copies to the original Python implementation.
    if output_bytes > 8 * 1024 * 1024 {
        return None;
    }
    Some(PyString::new(
        description.py(),
        &text.replace('\n', replacement),
    ))
}
