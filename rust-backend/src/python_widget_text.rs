//! Python bindings for poll and todo widget display text.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString};

use crate::widget_text::{
    PollParts, TodoParts, WidgetShorthand, parse_shorthand, poll_parts, todo_parts,
};

#[pyfunction]
pub(crate) fn extract_poll_widget_text(value: &Bound<'_, PyAny>) -> Option<PollParts> {
    let text = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(poll_parts(text))
}

#[pyfunction]
pub(crate) fn extract_todo_widget_text(value: &Bound<'_, PyAny>) -> Option<TodoParts> {
    let text = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(todo_parts(text))
}

type ShorthandPayload<'py> = (Option<&'static str>, Option<Bound<'py, PyDict>>);

/// The outer None declines; (None, None) is a successful non-widget result.
#[pyfunction]
pub(crate) fn parse_widget_shorthand<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Option<ShorthandPayload<'py>>> {
    let Ok(value) = value.cast_exact::<PyString>() else {
        return Ok(None);
    };
    let Ok(text) = value.to_str() else {
        return Ok(None);
    };
    let (kind, data) = match parse_shorthand(text) {
        WidgetShorthand::NotWidget => return Ok(Some((None, None))),
        WidgetShorthand::Poll((question, options)) => {
            let data = PyDict::new(py);
            data.set_item("question", question)?;
            data.set_item("options", options)?;
            ("poll", data)
        }
        WidgetShorthand::Todo((title, rows)) => {
            let data = PyDict::new(py);
            let tasks = rows
                .into_iter()
                .map(|(task, desc)| -> PyResult<_> {
                    let row = PyDict::new(py);
                    row.set_item("task", task)?;
                    row.set_item("desc", desc)?;
                    Ok(row)
                })
                .collect::<PyResult<Vec<_>>>()?;
            data.set_item("task_list_title", title)?;
            data.set_item("tasks", tasks)?;
            ("todo", data)
        }
    };
    Ok(Some((Some(kind), Some(data))))
}
