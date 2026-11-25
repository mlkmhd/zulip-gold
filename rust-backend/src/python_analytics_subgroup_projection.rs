//! Project preselected chart labels onto normalized time tokens.

use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString, PyTuple};

use crate::python_text_bytes::python_string_bytes;

type Group = Option<Vec<u8>>;

fn subgroup(value: &Bound<'_, PyAny>) -> Option<Group> {
    if value.is_none() {
        Some(None)
    } else {
        Some(Some(
            python_string_bytes(value.cast_exact::<PyString>().ok()?)?.into_owned(),
        ))
    }
}

fn time_token(value: &Bound<'_, PyAny>) -> Option<i64> {
    value.cast_exact::<PyInt>().ok()?.extract().ok()
}

type PointRows<'py> = HashMap<Group, HashMap<i64, Bound<'py, PyInt>>>;

fn parse_rows<'py>(rows: &Bound<'py, PyAny>) -> Option<PointRows<'py>> {
    let mut points = PointRows::new();
    for row in rows.cast_exact::<PyList>().ok()?.iter() {
        let row = row.cast_exact::<PyTuple>().ok()?;
        if row.len() != 3 {
            return None;
        }
        let group = subgroup(&row.get_item(0).ok()?)?;
        let time = time_token(&row.get_item(1).ok()?)?;
        let count = row.get_item(2).ok()?.cast_into_exact::<PyInt>().ok()?;
        // Like Python's inner dict, the last row wins.
        points.entry(group).or_default().insert(time, count);
    }
    Some(points)
}

#[pyfunction]
pub(crate) fn project_analytics_subgroup_series<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
    times: &Bound<'py, PyAny>,
    eligible_labels: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let (Some(points), Ok(times), Ok(eligible_labels)) = (
        parse_rows(rows),
        times.cast_exact::<PyList>(),
        eligible_labels.cast_exact::<PyList>(),
    ) else {
        return Ok(None);
    };
    let Some(times) = times
        .iter()
        .map(|time| time_token(&time))
        .collect::<Option<Vec<_>>>()
    else {
        return Ok(None);
    };
    let output = PyDict::new(py);
    for item in eligible_labels.iter() {
        let Ok(item) = item.cast_exact::<PyTuple>() else {
            return Ok(None);
        };
        if item.len() != 2 {
            return Ok(None);
        }
        let Some(group) = subgroup(&item.get_item(0)?) else {
            return Ok(None);
        };
        let Ok(label) = item.get_item(1)?.cast_into_exact::<PyString>() else {
            return Ok(None);
        };
        let data = points.get(&group);
        let values = PyList::empty(py);
        for time in &times {
            match data.and_then(|rows| rows.get(time)) {
                Some(count) => values.append(count)?,
                None => values.append(0)?,
            }
        }
        output.set_item(label, values)?;
    }
    Ok(Some(output))
}
