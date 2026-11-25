//! Literal formatting over names and suffixes already chosen by Python.

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};
use pyo3::prelude::*;
use pyo3::types::{PyInt, PyString};

fn code_point_offsets(text: &[u8]) -> impl Iterator<Item = usize> + '_ {
    // A continuation byte never starts a Python code point.
    text.iter()
        .enumerate()
        .filter_map(|(index, &byte)| (byte & 0xc0 != 0x80).then_some(index))
}

#[pyfunction]
pub(crate) fn truncate_rocketchat_display_name<'py>(
    py: Python<'py>,
    name: &Bound<'py, PyAny>,
    max_length: &Bound<'_, PyAny>,
    suffix: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let name = name.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(name)?;
    let max_length = max_length
        .cast_exact::<PyInt>()
        .ok()?
        .extract::<i64>()
        .ok()?;
    let length = i64::try_from(code_point_offsets(&text).count()).ok()?;
    if length <= max_length {
        return Some(name.clone());
    }

    let suffix = python_string_bytes(suffix.cast_exact::<PyString>().ok()?)?;
    let suffix_length = i64::try_from(code_point_offsets(&suffix).count()).ok()?;
    let stop = max_length.checked_sub(suffix_length)?;
    // Python's negative slice stop is relative to the original length.
    let stop = if stop < 0 {
        (length + stop).max(0)
    } else {
        stop
    };
    let cut = code_point_offsets(&text)
        .nth(usize::try_from(stop).ok()?)
        .unwrap_or(text.len());
    let mut output = Vec::with_capacity(cut.checked_add(suffix.len())?);
    output.extend_from_slice(&text[..cut]);
    output.extend_from_slice(&suffix);
    python_string_from_bytes(py, &output)
}

#[pyfunction]
pub(crate) fn prefix_rocketchat_team_display_name<'py>(
    name: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let name = name.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(name)?;
    let mut output = Vec::with_capacity(text.len().checked_add(b"[TEAM] ".len())?);
    output.extend_from_slice(b"[TEAM] ");
    output.extend_from_slice(&text);
    python_string_from_bytes(name.py(), &output)
}
