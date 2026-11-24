//! Decoration of already prepared plain-text message markup only.

use pyo3::prelude::*;
use pyo3::types::PyString;

#[pyfunction]
pub(crate) fn quote_prepared_email_plaintext(content: &Bound<'_, PyAny>) -> Option<String> {
    let content = content.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let mut output = String::with_capacity(content.len().saturating_add(2));
    // The Python reference splits on LF alone and prefixes every line,
    // including a final empty line and an entirely empty body.
    for (index, line) in content.split('\n').enumerate() {
        if index != 0 {
            output.push('\n');
        }
        output.push_str("> ");
        output.push_str(line);
    }
    Some(output)
}

#[pyfunction]
pub(crate) fn prepend_prepared_email_plaintext_sender(
    content: &Bound<'_, PyAny>,
    sender: &Bound<'_, PyAny>,
) -> Option<String> {
    let content = content.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let sender = sender.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("{sender}:\n{content}"))
}
