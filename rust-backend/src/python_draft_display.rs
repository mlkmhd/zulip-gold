//! Assemble the display of a draft after Django resolves the recipient and time.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString, PyTuple};

#[pyfunction]
pub(crate) fn render_prepared_draft<'py>(
    py: Python<'py>,
    prepared: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Ok(prepared) = prepared.cast_exact::<PyTuple>() else {
        return Ok(None);
    };
    if prepared.len() != 6 {
        return Ok(None);
    }
    let id = prepared.get_item(0)?;
    let kind = prepared.get_item(1)?;
    let to = prepared.get_item(2)?;
    let topic = prepared.get_item(3)?;
    let content = prepared.get_item(4)?;
    let timestamp = prepared.get_item(5)?;
    if !id.is_exact_instance_of::<PyInt>()
        || !kind.is_exact_instance_of::<PyString>()
        || !to.is_exact_instance_of::<PyList>()
        || !topic.is_exact_instance_of::<PyString>()
        || !content.is_exact_instance_of::<PyString>()
        || !timestamp.is_exact_instance_of::<PyInt>()
    {
        return Ok(None);
    }
    let row = PyDict::new(py);
    row.set_item("id", id)?;
    row.set_item("type", kind)?;
    row.set_item("to", to)?;
    row.set_item("topic", topic)?;
    row.set_item("content", content)?;
    row.set_item("timestamp", timestamp)?;
    Ok(Some(row))
}
