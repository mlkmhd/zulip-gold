//! Finish prepared display text without inspecting its rows.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PySlice, PyString, PyTuple};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

fn py_space(ch: char) -> bool {
    matches!(
        ch,
        '\u{9}'..='\u{d}' | '\u{1c}'..='\u{20}' | '\u{85}' | '\u{a0}' | '\u{1680}'
            | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}'
            | '\u{3000}'
    )
}

fn trim_python_whitespace(bytes: &[u8]) -> &[u8] {
    if let Ok(text) = std::str::from_utf8(bytes) {
        return text.trim_end_matches(py_space).as_bytes();
    }

    let mut end = bytes.len();
    while end > 0 {
        let mut start = end - 1;
        while start > 0 && bytes[start] & 0xc0 == 0x80 {
            start -= 1;
        }
        // Surrogate-pass bytes have valid code point boundaries. A single
        // UTF-8 decoding failure at this boundary is a surrogate, not space.
        if !std::str::from_utf8(&bytes[start..end])
            .ok()
            .and_then(|point| point.chars().next())
            .is_some_and(py_space)
        {
            break;
        }
        end = start;
    }
    &bytes[..end]
}

#[pyfunction]
pub(crate) fn finish_git_commit_summary_text<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
    omission: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let rows = rows.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(rows)?;
    if omission.is_none() {
        let trimmed = trim_python_whitespace(&text);
        return if trimmed.len() == text.len() {
            Some(rows.clone())
        } else {
            python_string_from_bytes(py, trimmed)
        };
    }

    let count = omission.cast_exact::<PyInt>().ok()?.extract::<u64>().ok()?;
    let suffix = if count == 0 {
        "[and more commit(s)]".to_owned()
    } else {
        format!("[and {count} more commit(s)]")
    };
    let mut result = text.into_owned();
    result.extend_from_slice(suffix.as_bytes());
    python_string_from_bytes(py, &result)
}

#[pyfunction]
pub(crate) fn render_prepared_git_commit_rows<'py>(
    commits: &Bound<'py, PyAny>,
    limit: &Bound<'py, PyAny>,
    template: &Bound<'py, PyAny>,
    short_sha: &Bound<'py, PyAny>,
    first_line: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = commits.py();
    let selected = commits.get_item(py.get_type::<PySlice>().call1((py.None(), limit))?)?;
    let mut rendered = PyString::new(py, "").into_any();
    let add = py.import("operator")?.getattr("iadd")?;
    for commit in selected.try_iter()? {
        let commit = commit?;
        let render_row = template.getattr("format")?;
        let fields = PyDict::new(py);
        fields.set_item(
            "commit_short_sha",
            short_sha.call1((commit.get_item("sha")?,))?,
        )?;
        fields.set_item("commit_url", commit.call_method1("get", ("url",))?)?;
        fields.set_item(
            "commit_msg",
            first_line.call1((commit.get_item("message")?,))?,
        )?;
        rendered = add.call1((rendered, render_row.call((), Some(&fields))?))?;
    }
    Ok(rendered)
}

#[pyfunction]
pub(crate) fn format_prepared_tag_event<'py>(
    user: &Bound<'py, PyAny>,
    tag: &Bound<'py, PyAny>,
    url: &Bound<'py, PyAny>,
    action: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let name = python_string_bytes(user.cast_exact::<PyString>().ok()?)?;
    let tag = python_string_bytes(tag.cast_exact::<PyString>().ok()?)?;
    let action = python_string_bytes(action.cast_exact::<PyString>().ok()?)?;
    let last = *tag.last()?;
    let url = if url.is_none() {
        None
    } else {
        Some(python_string_bytes(url.cast_exact::<PyString>().ok()?)?)
    };

    let mut result = Vec::new();
    result.extend_from_slice(&name);
    result.push(b' ');
    result.extend_from_slice(&action);
    result.extend_from_slice(b" tag ");
    if let Some(url) = url.filter(|url| !url.is_empty()) {
        result.push(b'[');
        result.extend_from_slice(&tag);
        result.extend_from_slice(b"](");
        result.extend_from_slice(&url);
        result.push(b')');
    } else {
        result.extend_from_slice(&tag);
    }
    if !last.is_ascii_punctuation() {
        result.push(b'.');
    }
    python_string_from_bytes(user.py(), &result)
}

#[pyfunction]
pub(crate) fn build_prepared_git_comment_message<'py>(
    data: &Bound<'py, PyTuple>,
    header_template: &Bound<'py, PyAny>,
    quote_template: &Bound<'py, PyAny>,
    short_sha: &Bound<'py, PyAny>,
    fence: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = data.py();
    let header = header_template.getattr("format")?;
    let fields = PyDict::new(py);
    fields.set_item("user_name", data.get_item(0)?)?;
    fields.set_item("action", data.get_item(1)?)?;
    fields.set_item("sha", short_sha.call1((data.get_item(3)?,))?)?;
    fields.set_item("url", data.get_item(2)?)?;
    let header = header.call((), Some(&fields))?;
    let message = data.get_item(4)?;
    let punctuation = if message.is_truthy()? { ":" } else { "." };
    let content = PyString::new(py, "{}{}").call_method1("format", (header, punctuation))?;
    if !message.is_truthy()? {
        return Ok(content);
    }

    let render_quote = quote_template.getattr("format")?;
    let fields = PyDict::new(py);
    fields.set_item("message", &message)?;
    fields.set_item("fence", fence.call1((message,))?)?;
    let quote = render_quote.call((), Some(&fields))?;
    py.import("operator")?
        .call_method1("iadd", (content, quote))
}
