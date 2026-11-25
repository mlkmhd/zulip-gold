//! Decoration of already prepared plain-text message markup only.

use pyo3::prelude::*;
use pyo3::types::PyString;

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

#[pyfunction]
pub(crate) fn quote_prepared_email_plaintext<'py>(
    py: Python<'py>,
    content: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let content = python_string_bytes(content.cast_exact::<PyString>().ok()?)?;
    let mut output = Vec::with_capacity(content.len().saturating_add(2));
    // The Python reference splits on LF alone and prefixes every line,
    // including a final empty line and an entirely empty body.
    for (index, line) in content.split(|byte| *byte == b'\n').enumerate() {
        if index != 0 {
            output.push(b'\n');
        }
        output.extend_from_slice(b"> ");
        output.extend_from_slice(line);
    }
    python_string_from_bytes(py, &output)
}

#[pyfunction]
pub(crate) fn prepend_prepared_email_plaintext_sender<'py>(
    py: Python<'py>,
    content: &Bound<'py, PyAny>,
    sender: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let content = python_string_bytes(content.cast_exact::<PyString>().ok()?)?;
    let sender = python_string_bytes(sender.cast_exact::<PyString>().ok()?)?;
    let mut output =
        Vec::with_capacity(sender.len().saturating_add(content.len()).saturating_add(2));
    output.extend_from_slice(&sender);
    output.extend_from_slice(b":\n");
    output.extend_from_slice(&content);
    python_string_from_bytes(py, &output)
}
