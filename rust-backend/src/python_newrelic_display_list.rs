//! Literal joining of already prepared New Relic display fragments.
use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

#[pyfunction]
pub(crate) fn join_newrelic_display_list<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let values = values.cast_exact::<PyList>().ok()?;
    // Match the plain Python join's singleton identity without processing text.
    if values.len() == 1 {
        return values.get_item(0).ok()?.cast_into_exact().ok();
    }
    let mut output = Vec::new();
    for (index, value) in values.iter().enumerate() {
        if index != 0 {
            output.extend_from_slice(b", ");
        }
        output.extend_from_slice(&python_string_bytes(value.cast_exact::<PyString>().ok()?)?);
    }
    python_string_from_bytes(py, &output)
}
