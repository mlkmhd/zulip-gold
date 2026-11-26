//! Add array framing to already serialized JSON export chunks.

use pyo3::exceptions::{PyAssertionError, PyStopIteration};
use pyo3::prelude::*;
use pyo3::pyclass::{PyTraverseError, PyVisit};
use pyo3::types::{PyBytes, PyDict, PyList};

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

type ExportPart = (&'static str, Option<Py<PyAny>>);

/// Plan one piece at a time. A second batch is pulled before its comma,
/// but is not delivered for encoding until the caller requests the next piece.
#[pyclass]
pub(crate) struct PreparedExportParts {
    batches: Option<Py<PyAny>>,
    pending: Option<Py<PyAny>>,
    first: bool,
    done: bool,
}

#[pymethods]
impl PreparedExportParts {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        if let Some(batches) = &self.batches {
            visit.call(batches)?;
        }
        if let Some(pending) = &self.pending {
            visit.call(pending)?;
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        self.done = true;
        self.pending = None;
        self.batches = None;
    }

    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self, py: Python<'_>) -> PyResult<Option<ExportPart>> {
        if self.done {
            return Ok(None);
        }
        if let Some(batch) = self.pending.take() {
            return Ok(Some(("following", Some(batch))));
        }
        let Some(batches) = &self.batches else {
            return Ok(None);
        };
        match batches.bind(py).call_method0("__next__") {
            Ok(batch) if self.first => {
                self.first = false;
                Ok(Some(("first", Some(batch.unbind()))))
            }
            Ok(batch) => {
                self.pending = Some(batch.unbind());
                Ok(Some(("separator", None)))
            }
            Err(error) => {
                self.done = true;
                if error.is_instance_of::<PyStopIteration>(py) {
                    Ok(Some((if self.first { "empty" } else { "end" }, None)))
                } else {
                    Err(error)
                }
            }
        }
    }
}

#[pyfunction]
pub(crate) fn plan_prepared_export_parts(
    batches: &Bound<'_, PyAny>,
) -> PyResult<Py<PreparedExportParts>> {
    Py::new(
        batches.py(),
        PreparedExportParts {
            batches: Some(batches.try_iter()?.into_any().unbind()),
            pending: None,
            first: true,
            done: false,
        },
    )
}

#[pyfunction]
pub(crate) fn format_prepared_export_datetimes<'py>(
    record: &Bound<'py, PyAny>,
    fields: &Bound<'py, PyAny>,
    datetime_type: &Bound<'py, PyAny>,
    is_naive: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let (Ok(record), Ok(fields)) = (record.cast_exact::<PyDict>(), fields.cast_exact::<PyList>())
    else {
        return Ok(None);
    };
    let updates = PyDict::new(record.py());
    for field in fields.iter() {
        let value = record.as_any().get_item(&field)?;
        if value.is_none() {
            continue;
        }
        if !value.is_instance(datetime_type)? || is_naive.call1((&value,))?.is_truthy()? {
            return Err(PyAssertionError::new_err(()));
        }
        updates.set_item(field, value.call_method0("timestamp")?)?;
    }
    // Copy only after reading timestamps, as Python does for {**record, **updates}.
    let output = record.copy()?;
    for (field, timestamp) in updates.iter() {
        output.set_item(field, timestamp)?;
    }
    Ok(Some(output))
}
