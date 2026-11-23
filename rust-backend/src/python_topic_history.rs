//! The caller has already selected visible rows using the original Django queries.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString, PyTuple};

use crate::topic_history::NewestTopicHistory;

pub(crate) fn topic_key(topic: &Bound<'_, PyString>) -> Option<String> {
    let spelling = topic.to_str().ok()?;
    if spelling.is_ascii() {
        return Some(spelling.to_ascii_lowercase());
    }
    // Python lowercasing is contextual for characters such as Greek sigma and
    // tied to the interpreter's Unicode database.
    topic.call_method0("lower").ok()?.extract().ok()
}

#[pyfunction]
pub(crate) fn format_visible_topic_history<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
    allow_empty: &Bound<'py, PyAny>,
    empty_fallback: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    let (Ok(rows), Ok(allow_empty), Ok(fallback)) = (
        rows.cast_exact::<PyList>(),
        allow_empty.cast_exact::<PyBool>(),
        empty_fallback.cast_exact::<PyString>(),
    ) else {
        return Ok(None);
    };
    let mut history = NewestTopicHistory::new((!allow_empty.is_true()).then(|| fallback.clone()));
    let mut previous = None;
    for row in rows.iter() {
        let Some((name, max_id)) = ordinary_row(&row) else {
            return Ok(None);
        };
        // Both SQL queries already order by the greatest message ID in each
        // group. Distinct groups cannot share a maximum DB message ID.
        // Keep the Python sort and tie behavior for other callers.
        if max_id <= 0 || previous.is_some_and(|previous| previous <= max_id) {
            return Ok(None);
        }
        previous = Some(max_id);
        let Some(key) = topic_key(&name) else {
            return Ok(None);
        };
        let is_empty = name.is_empty()?;
        history.push(name, key, is_empty, max_id);
    }

    let output = PyList::empty(py);
    let name_key = PyString::intern(py, "name");
    let id_key = PyString::intern(py, "max_id");
    for topic in history.finish() {
        let row = PyDict::new(py);
        row.set_item(&name_key, topic.name)?;
        row.set_item(&id_key, topic.max_id)?;
        output.append(row)?;
    }
    Ok(Some(output))
}

fn ordinary_row<'py>(row: &Bound<'py, PyAny>) -> Option<(Bound<'py, PyString>, i64)> {
    let row = row.cast_exact::<PyTuple>().ok()?;
    if row.len() != 2 {
        return None;
    }
    let name = row.get_item(0).ok()?.cast_into_exact::<PyString>().ok()?;
    let max_id = row.get_item(1).ok()?;
    let max_id = max_id.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    Some((name, max_id))
}
