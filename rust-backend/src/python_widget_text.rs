//! Extract titles and ordered rows from already selected widget text.

use pyo3::prelude::*;
use pyo3::types::PyString;

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

#[pyfunction]
pub(crate) fn extract_poll_widget_text(value: &Bound<'_, PyAny>) -> Option<(String, Vec<String>)> {
    let text = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let (title, options) = widget_parts(text);
    Some((title.to_owned(), options.map(str::to_owned).collect()))
}

type TodoParts = (String, Vec<(String, String)>);

#[pyfunction]
pub(crate) fn extract_todo_widget_text(value: &Bound<'_, PyAny>) -> Option<TodoParts> {
    let text = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
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
    Some((title.to_owned(), tasks))
}
