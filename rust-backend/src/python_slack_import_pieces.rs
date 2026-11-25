//! Trim and join already selected Slack conversion pieces.

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes, trim_python_text};
use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

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
    let separator = separator.as_bytes();
    let mut output = Vec::new();
    for piece in pieces.iter() {
        let piece = piece.cast_into_exact::<PyString>().ok()?;
        let bytes = python_string_bytes(&piece)?;
        let stripped = trim_python_text(&bytes);
        if stripped.is_empty() {
            continue;
        }
        if !output.is_empty() {
            output.extend_from_slice(separator);
            output.extend_from_slice(stripped);
        } else if let Some(previous) = first.take() {
            let previous = python_string_bytes(&previous)?;
            output.extend_from_slice(trim_python_text(&previous));
            output.extend_from_slice(separator);
            output.extend_from_slice(stripped);
        } else {
            first = Some(piece);
        }
    }
    if let Some(first) = first {
        let original = python_string_bytes(&first)?;
        let stripped = trim_python_text(&original);
        if stripped.len() == original.len() {
            return Some(first);
        }
        return python_string_from_bytes(py, stripped);
    }
    python_string_from_bytes(py, &output)
}

#[cfg(test)]
mod tests {
    use super::trim_python_text;

    #[test]
    fn trims_python_edges_without_altering_internal_whitespace() {
        assert_eq!(
            trim_python_text("\u{1f} فارسی\t😀\u{85}".as_bytes()),
            "فارسی\t😀".as_bytes()
        );
        assert_eq!(
            trim_python_text("\u{200b}\u{feff}".as_bytes()),
            "\u{200b}\u{feff}".as_bytes()
        );
    }
}
