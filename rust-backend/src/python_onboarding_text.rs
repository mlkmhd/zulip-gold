//! Format already prepared onboarding and announcement text.

use pyo3::prelude::*;
use pyo3::types::PyString;

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

fn starts_list_line(line: &[u8]) -> bool {
    if line.starts_with(b"- ") || line.starts_with(b"* ") {
        return true;
    }
    let digits = line.iter().take_while(|byte| byte.is_ascii_digit()).count();
    digits != 0 && line[digits..].starts_with(b". ")
}

#[pyfunction]
pub(crate) fn flatten_prepared_onboarding_newlines<'py>(
    content: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let content = content.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(content)?;
    let start = text.iter().take_while(|byte| **byte == b'\n').count();
    let end = text
        .iter()
        .rposition(|byte| *byte != b'\n')
        .map_or(start, |position| position + 1);
    let bytes = &text[start..end];
    let mut output = None::<Vec<u8>>;
    let mut written = 0;
    for (index, byte) in bytes.iter().enumerate() {
        if *byte != b'\n' || (index > 0 && bytes[index - 1] == b'\n') {
            continue;
        }
        let next = &bytes[index + 1..];
        if next.first() == Some(&b'\n') || starts_list_line(next) {
            continue;
        }
        // Inspect original byte neighbors like the Python lookarounds.
        // Scanning only LF and ASCII spaces keeps every slice on a UTF-8 boundary.
        let rendered = output.get_or_insert_with(|| Vec::with_capacity(bytes.len()));
        rendered.extend_from_slice(&bytes[written..index]);
        rendered.push(b' ');
        written = index + 1 + next.iter().take_while(|byte| **byte == b' ').count();
    }
    if let Some(mut rendered) = output {
        rendered.extend_from_slice(&bytes[written..]);
        return python_string_from_bytes(content.py(), &rendered);
    }
    if bytes.len() == text.len() {
        Some(content.clone())
    } else {
        python_string_from_bytes(content.py(), bytes)
    }
}

#[pyfunction]
pub(crate) fn wrap_prepared_welcome_quote<'py>(
    py: Python<'py>,
    introduction: &Bound<'py, PyAny>,
    fence: &Bound<'py, PyAny>,
    body: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let introduction = python_string_bytes(introduction.cast_exact::<PyString>().ok()?)?;
    let fence = python_string_bytes(fence.cast_exact::<PyString>().ok()?)?;
    let body = python_string_bytes(body.cast_exact::<PyString>().ok()?)?;
    let mut output = Vec::new();
    for part in [
        &b"\n"[..],
        &introduction,
        b"\n",
        &fence,
        b"quote\n",
        &body,
        b"\n",
        &fence,
        b"\n",
    ] {
        output.extend_from_slice(part);
    }
    python_string_from_bytes(py, &output)
}
