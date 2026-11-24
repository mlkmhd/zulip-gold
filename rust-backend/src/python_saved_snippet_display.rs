//! Presentation of already prepared saved-snippet scalar rows.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString, PyTuple};

struct Row<'py> {
    id: Bound<'py, PyInt>,
    title: Bound<'py, PyString>,
    content: Bound<'py, PyString>,
    date: Bound<'py, PyInt>,
}

impl<'py> Row<'py> {
    fn parse(value: &Bound<'py, PyAny>) -> Option<Self> {
        let fields = value.cast_exact::<PyTuple>().ok()?;
        if fields.len() != 4 {
            return None;
        }
        Some(Self {
            id: fields.get_item(0).ok()?.cast_into_exact().ok()?,
            title: fields.get_item(1).ok()?.cast_into_exact().ok()?,
            content: fields.get_item(2).ok()?.cast_into_exact().ok()?,
            date: fields.get_item(3).ok()?.cast_into_exact().ok()?,
        })
    }

    fn render(self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let result = PyDict::new(py);
        result.set_item("id", self.id)?;
        result.set_item("title", self.title)?;
        result.set_item("content", self.content)?;
        result.set_item("date_created", self.date)?;
        Ok(result)
    }
}

#[pyfunction]
pub(crate) fn render_saved_snippet_display_rows<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    let Ok(rows) = rows.cast_exact::<PyList>() else {
        return Ok(None);
    };
    let output = PyList::empty(py);
    for value in rows.iter() {
        let Some(row) = Row::parse(&value) else {
            return Ok(None);
        };
        output.append(row.render(py)?)?;
    }
    Ok(Some(output))
}
