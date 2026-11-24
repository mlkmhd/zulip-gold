//! Literal joining of already prepared New Relic display fragments.
use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

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
    let mut output = String::new();
    for (index, value) in values.iter().enumerate() {
        if index != 0 {
            output.push_str(", ");
        }
        output.push_str(value.cast_exact::<PyString>().ok()?.to_str().ok()?);
    }
    Some(PyString::new(py, &output))
}
