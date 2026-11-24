//! Display dictionaries over opaque, already prepared navigation scalars.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyList, PyString, PyTuple};

struct Row<'py> {
    fragment: Bound<'py, PyString>,
    pinned: Bound<'py, PyBool>,
    name: Bound<'py, PyAny>,
}

impl<'py> Row<'py> {
    fn parse(input: &Bound<'py, PyAny>) -> Option<Self> {
        let row = input.cast_exact::<PyTuple>().ok()?;
        if row.len() != 3 {
            return None;
        }
        let name = row.get_item(2).ok()?;
        if !name.is_none() && name.cast_exact::<PyString>().is_err() {
            return None;
        }
        Some(Self {
            fragment: row.get_item(0).ok()?.cast_into_exact().ok()?,
            pinned: row.get_item(1).ok()?.cast_into_exact().ok()?,
            name,
        })
    }

    fn render(self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let output = PyDict::new(py);
        output.set_item("fragment", self.fragment)?;
        output.set_item("is_pinned", self.pinned)?;
        output.set_item("name", self.name)?;
        Ok(output)
    }
}

#[pyfunction]
pub(crate) fn render_navigation_view_display_rows<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    let Ok(rows) = rows.cast_exact::<PyList>() else {
        return Ok(None);
    };
    let output = PyList::empty(py);
    for row in rows.iter() {
        let Some(row) = Row::parse(&row) else {
            return Ok(None);
        };
        output.append(row.render(py)?)?;
    }
    Ok(Some(output))
}
