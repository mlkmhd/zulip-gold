//! Assemble already selected edit history rows without owning rendering or access.

use pyo3::exceptions::PyAssertionError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyList, PyTuple};

fn timeline_entry<'py>(
    content: &Bound<'py, PyAny>,
    rendered: &Bound<'py, PyAny>,
    timestamp: &Bound<'py, PyAny>,
    topic: &Bound<'py, PyAny>,
    user: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let row = PyDict::new(content.py());
    row.set_item("content", content)?;
    row.set_item("rendered_content", rendered)?;
    row.set_item("timestamp", timestamp)?;
    row.set_item("topic", topic)?;
    row.set_item("user_id", user)?;
    Ok(row)
}

#[pyfunction]
pub(crate) fn assemble_prepared_edit_history<'py>(
    rows: &Bound<'py, PyAny>,
    current: &Bound<'py, PyTuple>,
    plain_links: &Bound<'py, PyAny>,
    callbacks: &Bound<'py, PyTuple>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    let (Ok(rows), Ok(plain_links)) = (
        rows.cast_exact::<PyList>(),
        plain_links.cast_exact::<PyBool>(),
    ) else {
        return Ok(None);
    };
    let Some(rows) = rows
        .iter()
        .map(|row| row.cast_into_exact::<PyDict>().ok())
        .collect::<Option<Vec<_>>>()
    else {
        return Ok(None);
    };

    let mut content = current.get_item(0)?;
    let mut rendered = current.get_item(1)?;
    let mut topic = current.get_item(2)?;
    let rename_topic = callbacks.get_item(0)?;
    let render_plain = callbacks.get_item(1)?;
    let highlight_diff = callbacks.get_item(2)?;
    let read_original = callbacks.get_item(3)?;
    let use_plain_links = plain_links.is_true();
    let entries = PyList::empty(current.py());

    for row in rows {
        let row = row.as_any();
        let timestamp = row.get_item("timestamp")?;
        let user = row.get_item("user_id")?;
        let entry = timeline_entry(&content, &rendered, &timestamp, &topic, &user)?;

        if row.contains("prev_topic")? {
            topic = rename_topic.call1((row.get_item("prev_topic")?,))?;
            entry.set_item("prev_topic", &topic)?;
        }
        if row.contains("prev_content")? {
            content = row.get_item("prev_content")?;
            entry.set_item("prev_content", &content)?;
            let mut previous_rendered = row.get_item("prev_rendered_content")?;
            if previous_rendered.is_none() {
                return Err(PyAssertionError::new_err(()));
            }
            if use_plain_links {
                previous_rendered = render_plain.call1((previous_rendered,))?;
            }
            entry.set_item("prev_rendered_content", &previous_rendered)?;
            if rendered.is_none() {
                return Err(PyAssertionError::new_err(()));
            }
            let diff = highlight_diff.call1((&previous_rendered, &rendered))?;
            entry.set_item("content_html_diff", diff)?;
            rendered = previous_rendered;
        }
        if row.contains("prev_stream")? {
            entry.set_item("prev_stream", row.get_item("prev_stream")?)?;
            entry.set_item("stream", row.get_item("stream")?)?;
        }
        entries.append(entry)?;
    }

    let original = read_original.call0()?.cast_into::<PyTuple>()?;
    entries.append(timeline_entry(
        &content,
        &rendered,
        &original.get_item(0)?,
        &topic,
        &original.get_item(1)?,
    )?)?;
    Ok(Some(entries))
}
