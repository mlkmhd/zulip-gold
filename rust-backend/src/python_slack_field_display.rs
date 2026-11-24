//! Literal Slack-import display assembly over Python-prepared fields.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

fn table_cell<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    let text = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
    // These are inputs the host has already cleaned. Do not clean or
    // reinterpret them here.
    (!text.bytes().any(|byte| matches!(byte, b'\n' | b'|'))).then_some(text)
}

#[pyfunction]
pub(crate) fn assemble_prepared_slack_section_table(fields: &Bound<'_, PyAny>) -> Option<String> {
    let fields = fields.cast_exact::<PyList>().ok()?;
    let mut fields = fields.iter();
    let mut output = String::from("| | |\n|-|-|\n");
    while let Some(left_value) = fields.next() {
        let left = table_cell(&left_value)?;
        let right_value = fields.next();
        let right = if let Some(value) = &right_value {
            table_cell(value)?
        } else {
            ""
        };
        output.push_str("| ");
        output.push_str(left);
        output.push_str(" | ");
        output.push_str(right);
        output.push_str(" |\n");
    }
    Some(output)
}

#[pyfunction]
pub(crate) fn join_prepared_slack_attachment_fields<'py>(
    fields: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let fields = fields.cast_exact::<PyList>().ok()?;
    if fields.len() == 1 {
        let field = fields.get_item(0).ok()?;
        return Some(field.cast_exact::<PyString>().ok()?.clone());
    }
    let mut output = String::new();
    for (index, field) in fields.iter().enumerate() {
        let field = field.cast_exact::<PyString>().ok()?.to_str().ok()?;
        if index != 0 {
            output.push('\n');
        }
        output.push_str(field);
    }
    Some(PyString::new(fields.py(), &output))
}
