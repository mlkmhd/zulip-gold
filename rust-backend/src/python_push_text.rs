//! Plain-text formatting after Django extracts a mobile notification preview.

use std::io::Write;

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyInt, PyList, PyString, PyTuple};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes, trim_python_text};

fn is_line_boundary(character: char) -> bool {
    matches!(
        character,
        '\n' | '\r'
            | '\u{b}'
            | '\u{c}'
            | '\u{1c}'
            | '\u{1d}'
            | '\u{1e}'
            | '\u{85}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

fn append_quote_line(output: &mut Vec<u8>, line: &[u8]) {
    if !line.is_empty() {
        output.extend_from_slice(b"> ");
        output.extend_from_slice(line);
        output.push(b'\n');
    }
}

#[pyfunction]
pub(crate) fn quote_mobile_push_text<'py>(
    input: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let input = input.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(input)?;
    let mut output = Vec::with_capacity(text.len());
    let mut offset = 0;
    let mut line_start = 0;
    for chunk in text.utf8_chunks() {
        for (index, ch) in chunk.valid().char_indices() {
            if is_line_boundary(ch) {
                let end = offset + index;
                append_quote_line(&mut output, &text[line_start..end]);
                line_start = end + ch.len_utf8();
            }
        }
        // Preserve lone surrogates in the line. CRLF may split twice,
        // which is equivalent because empty lines are never quoted.
        offset += chunk.valid().len() + chunk.invalid().len();
    }
    append_quote_line(&mut output, &text[line_start..]);
    python_string_from_bytes(input.py(), &output)
}

#[pyfunction]
pub(crate) fn truncate_mobile_push_text<'py>(
    input: &Bound<'py, PyAny>,
) -> Option<(Bound<'py, PyString>, bool)> {
    let input = input.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(input)?;
    // Count Python code points, including lone surrogates, not graphemes.
    let Some((end, _)) = text
        .iter()
        .enumerate()
        .filter(|(_, byte)| **byte & 0xc0 != 0x80)
        .nth(200)
    else {
        return Some((input.clone(), false));
    };
    let mut output = Vec::with_capacity(end + '…'.len_utf8());
    output.extend_from_slice(&text[..end]);
    output.extend_from_slice("…".as_bytes());
    Some((python_string_from_bytes(input.py(), &output)?, true))
}

#[pyfunction]
pub(crate) fn format_mobile_ordered_list<'py>(
    rows: &Bound<'py, PyAny>,
    start: &Bound<'_, PyAny>,
    depth: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let rows = rows.cast_exact::<PyList>().ok()?;
    let start = start.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let depth = depth.cast_exact::<PyInt>().ok()?.extract::<usize>().ok()?;
    if depth > 256 {
        return None;
    }
    start.checked_add(i64::try_from(rows.len().saturating_sub(1)).ok()?)?;

    let indent = if depth == 0 {
        String::new()
    } else {
        format!("\n{}", "  ".repeat(depth))
    };
    let mut output = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let row = python_string_bytes(row.cast_exact::<PyString>().ok()?)?;
        if index != 0 {
            output.push(b'\n');
        }
        output.extend_from_slice(indent.as_bytes());
        let number = start + i64::try_from(index).ok()?;
        write!(output, "{number}. ").ok()?;
        output.extend_from_slice(trim_python_text(&row));
    }
    python_string_from_bytes(rows.py(), &output)
}

#[pyfunction]
pub(crate) fn format_mobile_spoiler_header<'py>(
    header: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let header = header.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(header)?;
    let text = trim_python_text(&text);
    let mut preview = Vec::with_capacity(text.len().saturating_add(7));
    if !text.is_empty() {
        preview.extend_from_slice(text);
        preview.push(b' ');
    }
    preview.extend_from_slice("(…)\n".as_bytes());
    python_string_from_bytes(header.py(), &preview)
}

#[pyfunction]
pub(crate) fn plan_mobile_quote_collapse(statuses: &Bound<'_, PyAny>) -> Option<Vec<Option<bool>>> {
    let statuses = statuses.cast_exact::<PyList>().ok()?;
    let mut has_quote = false;
    let mut has_unquoted = false;
    let mut previous = false;
    let mut actions = Vec::with_capacity(statuses.len());
    for status in statuses.iter() {
        let current = status.cast_exact::<PyBool>().ok()?.extract::<bool>().ok()?;
        has_quote |= current;
        has_unquoted |= !current;
        // None keeps the element, true drops it, false replaces it with the marker.
        actions.push(current.then_some(previous));
        previous = current;
    }
    if !has_quote || !has_unquoted {
        actions.clear();
    }
    Some(actions)
}

#[pyfunction]
pub(crate) fn render_plain_mobile_preview_tree<'py>(
    rows: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let rows = rows.cast_exact::<PyList>().ok()?;
    if rows.is_empty() || rows.len() > 512 {
        return None;
    }
    let mut output = Vec::new();
    let mut tails: Vec<Bound<'py, PyString>> = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let row = row.cast_exact::<PyTuple>().ok()?;
        if row.len() != 3 {
            return None;
        }
        let depth: usize = row
            .get_item(0)
            .ok()?
            .cast_exact::<PyInt>()
            .ok()?
            .extract()
            .ok()?;
        if (index == 0 && depth != 0)
            || (index > 0 && depth == 0)
            || depth > tails.len()
            || depth > 128
        {
            return None;
        }
        while tails.len() > depth {
            output.extend_from_slice(&python_string_bytes(&tails.pop()?)?);
        }
        let text = row.get_item(1).ok()?.cast_into_exact::<PyString>().ok()?;
        let tail = row.get_item(2).ok()?.cast_into_exact::<PyString>().ok()?;
        output.extend_from_slice(&python_string_bytes(&text)?);
        tails.push(tail);
    }
    for tail in tails.iter().rev() {
        output.extend_from_slice(&python_string_bytes(tail)?);
    }
    python_string_from_bytes(rows.py(), &output)
}
