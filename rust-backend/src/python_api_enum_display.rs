//! Comma-joining of already-formatted API documentation examples.

use crate::python_text_bytes::join_exact_python_strings;
use pyo3::prelude::*;
use pyo3::types::PyString;

#[pyfunction]
pub(crate) fn join_prepared_api_enum_examples<'py>(
    parts: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    join_exact_python_strings(parts, b", ")
}
