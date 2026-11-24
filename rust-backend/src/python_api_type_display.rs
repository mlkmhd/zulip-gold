//! Literal label assembly for API documentation, over values prepared by Python.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

#[pyfunction]
pub(crate) fn join_prepared_doc_type_labels<'py>(
    values: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let values = values.cast_exact::<PyList>().ok()?;
    if values.len() == 1 {
        let only = values.get_item(0).ok()?;
        return Some(only.cast_exact::<PyString>().ok()?.clone());
    }

    let mut output = String::new();
    for (index, value) in values.iter().enumerate() {
        let text = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
        if index != 0 {
            output.push_str(" | ");
        }
        output.push_str(text);
    }
    Some(PyString::new(values.py(), &output))
}

#[pyfunction]
pub(crate) fn wrap_prepared_doc_array_label(value: &Bound<'_, PyAny>) -> Option<String> {
    let text = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("({text})[]"))
}

#[pyfunction]
pub(crate) fn append_doc_nullable_label(value: &Bound<'_, PyAny>) -> Option<String> {
    let text = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("{text} | null"))
}
