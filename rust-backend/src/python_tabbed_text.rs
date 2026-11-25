//! Fixed text transformations over Python-selected documentation tab content.

use crate::python_text_bytes::{
    join_exact_python_strings, python_string_bytes, python_string_from_bytes, trim_python_text,
};
use pyo3::prelude::*;
use pyo3::types::PyString;

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
