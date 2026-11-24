//! Literal rendering of selected non-file Jotform display values.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

#[pyfunction]
pub(crate) fn join_jotform_display_values<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let values = values.cast_exact::<PyList>().ok()?;
    // CPython's join returns the original exact string for a singleton.
    // That also preserves an existing string with unpaired surrogates.
    if values.len() == 1 {
        return values.get_item(0).ok()?.cast_into_exact().ok();
    }
    let mut output = String::new();
    for (index, value) in values.iter().enumerate() {
        if index != 0 {
            output.push(' ');
        }
        output.push_str(value.cast_exact::<PyString>().ok()?.to_str().ok()?);
    }
    Some(PyString::new(py, &output))
}
