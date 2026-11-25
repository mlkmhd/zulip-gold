//! Parse display data for the poll and todo widget shorthands.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString};

fn py_space(ch: char) -> bool {
    matches!(
        ch,
        '\u{9}'..='\u{d}' | '\u{1c}'..='\u{20}' | '\u{85}' | '\u{a0}' | '\u{1680}'
            | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}'
            | '\u{3000}'
    )
}

fn py_lines(text: &str) -> impl Iterator<Item = &str> {
    let mut chars = text.char_indices().peekable();
    let mut from = 0;
    std::iter::from_fn(move || {
        while let Some((index, ch)) = chars.next() {
            if matches!(
                ch,
                '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}'
                    ..='\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
            ) {
                let line = &text[from..index];
                from = index + ch.len_utf8();
                if ch == '\r' && chars.peek().is_some_and(|&(_, next)| next == '\n') {
                    chars.next();
                    from += 1;
                }
                return Some(line);
            }
        }
        if from == text.len() {
            None
        } else {
            let last = &text[from..];
            from = text.len();
            Some(last)
        }
    })
}

fn widget_parts(text: &str) -> (&str, impl Iterator<Item = &str>) {
    let mut lines = py_lines(text).peekable();
    let heading = if lines.peek().is_some_and(|line| !line.is_empty()) {
        lines.next().expect("peeked line").trim_matches(py_space)
    } else {
        ""
    };
    let rows = lines
        .map(|line| {
            let line = line.trim_matches(py_space);
            if let Some(rest) = line.strip_prefix(['-', '*']) {
                rest.trim_start_matches(py_space)
            } else {
                line
            }
        })
        .filter(|line| !line.is_empty());
    (heading, rows)
}

type PollParts = (String, Vec<String>);
type TodoParts = (String, Vec<(String, String)>);

fn poll_parts(text: &str) -> PollParts {
    let (title, options) = widget_parts(text);
    (title.to_owned(), options.map(str::to_owned).collect())
}

fn todo_parts(text: &str) -> TodoParts {
    let (title, tasks) = widget_parts(text);
    let tasks = tasks
        .map(|line| {
            let (task, description) = line.split_once(": ").unwrap_or((line, ""));
            (
                task.trim_matches(py_space).to_owned(),
                description.trim_matches(py_space).to_owned(),
            )
        })
        .collect();
    (title.to_owned(), tasks)
}

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

enum WidgetShorthand {
    NotWidget,
    Poll(PollParts),
    Todo(TodoParts),
}

fn widget_shorthand(text: &str) -> WidgetShorthand {
    // The first token is empty on leading whitespace, and whitespace after
    // a recognized command is part of the text handed to splitlines().
    let (command, rest) = text.split_at(text.find(py_space).unwrap_or(text.len()));
    match command {
        "/poll" => WidgetShorthand::Poll(poll_parts(rest)),
        "/todo" => WidgetShorthand::Todo(todo_parts(rest)),
        _ => WidgetShorthand::NotWidget,
    }
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
    let (kind, data) = match widget_shorthand(text) {
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
