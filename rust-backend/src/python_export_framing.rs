//! Add array framing to already serialized JSON export chunks.

use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pyfunction]
pub(crate) fn frame_prepared_export_json<'py>(
    kind: &str,
    indent: &Bound<'py, PyAny>,
    batch: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyBytes>> {
    let indent = indent.cast_exact::<PyBytes>().ok()?.as_bytes();
    let py = batch.py();
    match kind {
        "separator" => Some(PyBytes::new(py, &[&b",\n"[..], indent].concat())),
        "empty" => Some(PyBytes::new(py, b"[]")),
        "end" => Some(PyBytes::new(py, &[&b"\n"[..], indent, &b"]"[..]].concat())),
        "first" | "following" => {
            let text = batch.cast_exact::<PyBytes>().ok()?.as_bytes();
            let text = if kind == "following" {
                text.strip_prefix(b"[\n")?
            } else {
                text
            };
            let text = text.strip_suffix(b"\n]")?;
            if indent.is_empty() {
                return Some(PyBytes::new(py, text));
            }

            let mut out = Vec::with_capacity(text.len());
            for line in text.split_inclusive(|&value| value == b'\n') {
                out.extend_from_slice(line);
                if line.ends_with(b"\n") {
                    out.extend_from_slice(indent);
                }
            }
            Some(PyBytes::new(py, &out))
        }
        _ => None,
    }
}
