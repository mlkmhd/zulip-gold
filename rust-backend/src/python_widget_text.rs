//! Python bindings for poll and todo widget display text.

use std::borrow::Cow;

use pyo3::exceptions::{PyTypeError, PyUnicodeEncodeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyIterator, PyMemoryView, PyString};

use crate::widget_text::{
    PollParts, WidgetCommand, WidgetShorthand, parse_shorthand, poll_parts, split_task_row,
    split_widget_command, strip_list_marker, todo_parts,
};

const SURROGATE_ESCAPE: char = '\u{f0000}';
const SURROGATE_BASE: u32 = 0xd800;
const ESCAPED_BASE: u32 = SURROGATE_ESCAPE as u32 + 1;
const SURROGATE_COUNT: u32 = 0x800;

struct WidgetText<'a> {
    text: Cow<'a, str>,
    escaped: bool,
}

impl<'a> WidgetText<'a> {
    fn new(value: &'a Bound<'_, PyString>) -> PyResult<Self> {
        let error = match value.to_str() {
            Ok(text) => {
                return Ok(Self {
                    text: Cow::Borrowed(text),
                    escaped: false,
                });
            }
            Err(error) => error,
        };
        if !error.is_instance_of::<PyUnicodeEncodeError>(value.py()) {
            return Err(error);
        }

        // Python str permits surrogate code points that Rust str cannot hold.
        // Represent them temporarily by sequences of non-whitespace private-use
        // characters so they cannot be mistaken for any widget delimiter.
        // The parser only cuts at delimiters, so an escape pair stays intact.
        // Escape literal occurrences of the prefix as well.
        // Use the builtin descriptor rather than a subclass's encode method.
        let encoded = value
            .py()
            .get_type::<PyString>()
            .call_method1("encode", (value, "utf-32-le", "surrogatepass"))?
            .cast_into::<PyBytes>()?;
        let mut text = String::new();
        for chunk in encoded.as_bytes().chunks_exact(4) {
            let point = u32::from_le_bytes(chunk.try_into().expect("four-byte chunk"));
            if (SURROGATE_BASE..SURROGATE_BASE + SURROGATE_COUNT).contains(&point) {
                text.push(SURROGATE_ESCAPE);
                text.push(
                    char::from_u32(ESCAPED_BASE + point - SURROGATE_BASE)
                        .expect("mapped surrogate in private-use range"),
                );
            } else {
                let ch = char::from_u32(point)
                    .ok_or_else(|| PyValueError::new_err("Invalid Python string code point"))?;
                if ch == SURROGATE_ESCAPE {
                    text.push(ch);
                }
                text.push(ch);
            }
        }
        Ok(Self {
            text: Cow::Owned(text),
            escaped: true,
        })
    }

    fn result<'py>(&self, py: Python<'py>, text: &str) -> PyResult<Bound<'py, PyString>> {
        if !self.escaped || !text.contains(SURROGATE_ESCAPE) {
            return Ok(PyString::new(py, text));
        }

        let mut code_points = Vec::new();
        let mut chars = text.chars();
        while let Some(ch) = chars.next() {
            let point = if ch == SURROGATE_ESCAPE {
                match chars.next() {
                    Some(SURROGATE_ESCAPE) => SURROGATE_ESCAPE as u32,
                    Some(encoded)
                        if (ESCAPED_BASE..ESCAPED_BASE + SURROGATE_COUNT)
                            .contains(&(encoded as u32)) =>
                    {
                        SURROGATE_BASE + encoded as u32 - ESCAPED_BASE
                    }
                    _ => return Err(PyValueError::new_err("Invalid widget surrogate escape")),
                }
            } else {
                ch as u32
            };
            code_points.extend_from_slice(&point.to_le_bytes());
        }
        Ok(PyBytes::new(py, &code_points)
            .call_method1("decode", ("utf-32-le", "surrogatepass"))?
            .cast_into::<PyString>()?)
    }

    fn poll<'py>(&self, py: Python<'py>, (title, options): PollParts) -> PyResult<PollOutput<'py>> {
        let options = options
            .iter()
            .map(|option| self.result(py, option))
            .collect::<PyResult<Vec<_>>>()?;
        Ok((self.result(py, &title)?, options))
    }

    fn todo_row<'py>(
        &self,
        py: Python<'py>,
        (task, desc): (String, String),
    ) -> PyResult<TodoRow<'py>> {
        Ok((self.result(py, &task)?, self.result(py, &desc)?))
    }
}

type PollOutput<'py> = (Bound<'py, PyString>, Vec<Bound<'py, PyString>>);
type TodoRow<'py> = (Bound<'py, PyString>, Bound<'py, PyString>);
type PollExtraction<'py> = (Bound<'py, PyAny>, Vec<Bound<'py, PyString>>);
type TodoExtraction<'py> = (Bound<'py, PyAny>, Vec<TodoRow<'py>>);

// A str subclass may implement splitlines, or return str subclasses that
// implement strip. Dispatch those Python methods once, but apply the same
// Rust grammar to the resulting text as for exact strings.
fn subclass_lines<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyString>,
) -> PyResult<(Bound<'py, PyAny>, Bound<'py, PyIterator>)> {
    let lines = value.call_method0("splitlines")?;
    let title = if lines.is_truthy()? && lines.get_item(0)?.is_truthy()? {
        lines.call_method1("pop", (0,))?.call_method0("strip")?
    } else {
        PyString::new(py, "").into_any()
    };
    Ok((title, lines.try_iter()?))
}

fn stripped_subclass_line<'py>(line: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyString>> {
    let stripped = line.call_method0("strip")?;
    if let Ok(text) = stripped.cast::<PyString>() {
        return Ok(text.clone());
    }
    if PyMemoryView::from(&stripped).is_ok() {
        return Err(PyTypeError::new_err(
            "cannot use a string pattern on a bytes-like object",
        ));
    }
    let type_name = stripped.get_type().name()?;
    Err(PyTypeError::new_err(format!(
        "expected string or bytes-like object, got '{}'",
        type_name.to_str()?,
    )))
}

#[pyfunction]
pub(crate) fn extract_poll_widget_text<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Option<PollExtraction<'py>>> {
    let Ok(value) = value.cast::<PyString>() else {
        return Ok(None);
    };
    if value.is_exact_instance_of::<PyString>() {
        let text = WidgetText::new(value)?;
        let (title, options) = text.poll(py, poll_parts(&text.text))?;
        return Ok(Some((title.into_any(), options)));
    }
    let (title, lines) = subclass_lines(py, value)?;
    let mut options = Vec::new();
    for line in lines {
        let line = stripped_subclass_line(&line?)?;
        let text = WidgetText::new(&line)?;
        let option = strip_list_marker(&text.text);
        if !option.is_empty() {
            options.push(text.result(py, option)?);
        }
    }
    Ok(Some((title, options)))
}

#[pyfunction]
pub(crate) fn extract_todo_widget_text<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Option<TodoExtraction<'py>>> {
    let Ok(value) = value.cast::<PyString>() else {
        return Ok(None);
    };
    if value.is_exact_instance_of::<PyString>() {
        let text = WidgetText::new(value)?;
        let (title, rows) = todo_parts(&text.text);
        let rows = rows
            .into_iter()
            .map(|row| text.todo_row(py, row))
            .collect::<PyResult<Vec<_>>>()?;
        return Ok(Some((text.result(py, &title)?.into_any(), rows)));
    }
    let (title, lines) = subclass_lines(py, value)?;
    let mut tasks = Vec::new();
    for line in lines {
        let line = stripped_subclass_line(&line?)?;
        let text = WidgetText::new(&line)?;
        let row = strip_list_marker(&text.text);
        if !row.is_empty() {
            let (task, desc) = split_task_row(row);
            tasks.push((text.result(py, task)?, text.result(py, desc)?));
        }
    }
    Ok(Some((title, tasks)))
}

/// Classify a str subclass without invoking its replace override, which
/// the Python adapter will invoke once along with dataclass serialization.
#[pyfunction]
pub(crate) fn classify_widget_subclass(
    value: &Bound<'_, PyAny>,
) -> PyResult<Option<(Option<&'static str>,)>> {
    let Ok(value) = value.cast::<PyString>() else {
        return Ok(None);
    };
    if value.is_exact_instance_of::<PyString>() {
        return Ok(None);
    }
    let text = WidgetText::new(value)?;
    let (kind, _) = split_widget_command(&text.text);
    Ok(Some((kind.map(|kind| match kind {
        WidgetCommand::Poll => "poll",
        WidgetCommand::Todo => "todo",
    }),)))
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
    let text = WidgetText::new(value)?;
    let (kind, data) = match parse_shorthand(&text.text) {
        WidgetShorthand::NotWidget => return Ok(Some((None, None))),
        WidgetShorthand::Poll(parts) => {
            let (question, options) = text.poll(py, parts)?;
            let data = PyDict::new(py);
            data.set_item("question", question)?;
            data.set_item("options", options)?;
            ("poll", data)
        }
        WidgetShorthand::Todo((title, rows)) => {
            let data = PyDict::new(py);
            let tasks = rows
                .into_iter()
                .map(|parts| -> PyResult<_> {
                    let (task, desc) = text.todo_row(py, parts)?;
                    let row = PyDict::new(py);
                    row.set_item("task", task)?;
                    row.set_item("desc", desc)?;
                    Ok(row)
                })
                .collect::<PyResult<Vec<_>>>()?;
            data.set_item("task_list_title", text.result(py, &title)?)?;
            data.set_item("tasks", tasks)?;
            ("todo", data)
        }
    };
    Ok(Some((Some(kind), Some(data))))
}
