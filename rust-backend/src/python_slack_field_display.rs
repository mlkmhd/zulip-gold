//! Literal Slack-import display assembly over Python-prepared fields.

use std::borrow::Cow;

use crate::python_text_bytes::{
    join_exact_python_strings, python_string_bytes, python_string_from_bytes,
};
use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

fn table_cell<'a>(value: &'a Bound<'_, PyAny>) -> Option<Cow<'a, [u8]>> {
    let text = python_string_bytes(value.cast_exact::<PyString>().ok()?)?;
    // These are inputs the host has already cleaned. Do not clean or
    // reinterpret them here.
    (!text.iter().any(|&byte| matches!(byte, b'\n' | b'|'))).then_some(text)
}

#[pyfunction]
pub(crate) fn assemble_prepared_slack_section_table<'py>(
    fields: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let fields = fields.cast_exact::<PyList>().ok()?;
    let py = fields.py();
    let mut fields = fields.iter();
    let mut output = b"| | |\n|-|-|\n".to_vec();
    while let Some(left_value) = fields.next() {
        let left = table_cell(&left_value)?;
        let right_value = fields.next();
        let right = if let Some(value) = &right_value {
            table_cell(value)?
        } else {
            Cow::Borrowed(&b""[..])
        };
        output.extend_from_slice(b"| ");
        output.extend_from_slice(&left);
        output.extend_from_slice(b" | ");
        output.extend_from_slice(&right);
        output.extend_from_slice(b" |\n");
    }
    python_string_from_bytes(py, &output)
}

#[pyfunction]
pub(crate) fn join_prepared_slack_attachment_fields<'py>(
    fields: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    join_exact_python_strings(fields, b"\n")
}
