//! Format already prepared onboarding and announcement text.

use pyo3::prelude::*;
use pyo3::types::PyString;

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
    let text = content.to_str().ok()?;
    let trimmed = text.trim_matches('\n');
    let bytes = trimmed.as_bytes();
    let mut output = None::<String>;
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
        let rendered = output.get_or_insert_with(|| String::with_capacity(trimmed.len()));
        rendered.push_str(&trimmed[written..index]);
        rendered.push(' ');
        written = index + 1 + next.iter().take_while(|byte| **byte == b' ').count();
    }
    if let Some(mut rendered) = output {
        rendered.push_str(&trimmed[written..]);
        return Some(PyString::new(content.py(), &rendered));
    }
    if trimmed.len() == text.len() {
        Some(content.clone())
    } else {
        Some(PyString::new(content.py(), trimmed))
    }
}

#[pyfunction]
pub(crate) fn wrap_prepared_welcome_quote(
    introduction: &Bound<'_, PyAny>,
    fence: &Bound<'_, PyAny>,
    body: &Bound<'_, PyAny>,
) -> Option<String> {
    let introduction = introduction.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let fence = fence.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let body = body.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("\n{introduction}\n{fence}quote\n{body}\n{fence}\n"))
}
