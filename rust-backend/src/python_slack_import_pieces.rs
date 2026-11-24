//! Trim and join already selected Slack conversion pieces.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

fn trim_python_space(text: &str) -> &str {
    text.trim_matches(|ch| {
        matches!(
            ch,
            '\u{9}'..='\u{d}' | '\u{1c}'..='\u{20}' | '\u{85}' | '\u{a0}' | '\u{1680}'
                | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}'
                | '\u{3000}'
        )
    })
}

#[pyfunction]
pub(crate) fn join_slack_import_pieces<'py>(
    py: Python<'py>,
    pieces: &Bound<'py, PyAny>,
    separator: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let pieces = pieces.cast_exact::<PyList>().ok()?;
    let separator = separator.cast_exact::<PyString>().ok()?.to_str().ok()?;
    if !matches!(separator, "\n" | "\n\n") {
        return None;
    }

    let mut first: Option<Bound<'py, PyString>> = None;
    let mut output = String::new();
    for piece in pieces.iter() {
        let piece = piece.cast_into_exact::<PyString>().ok()?;
        let stripped = trim_python_space(piece.to_str().ok()?);
        if stripped.is_empty() {
            continue;
        }
        if !output.is_empty() {
            output.push_str(separator);
            output.push_str(stripped);
        } else if let Some(previous) = first.take() {
            output.push_str(trim_python_space(previous.to_str().ok()?));
            output.push_str(separator);
            output.push_str(stripped);
        } else {
            first = Some(piece);
        }
    }
    if let Some(first) = first {
        let original = first.to_str().ok()?;
        let stripped = trim_python_space(original);
        if stripped.len() == original.len() {
            return Some(first);
        }
        return Some(PyString::new(py, stripped));
    }
    Some(PyString::new(py, &output))
}

#[cfg(test)]
mod tests {
    use super::trim_python_space;

    #[test]
    fn trims_python_edges_without_altering_internal_whitespace() {
        assert_eq!(trim_python_space("\u{1f} فارسی\t😀\u{85}"), "فارسی\t😀");
        assert_eq!(trim_python_space("\u{200b}\u{feff}"), "\u{200b}\u{feff}");
    }
}
