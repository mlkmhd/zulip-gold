//! Literal formatting over names and suffixes already chosen by Python.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyString};

#[pyfunction]
pub(crate) fn truncate_rocketchat_display_name<'py>(
    py: Python<'py>,
    name: &Bound<'py, PyAny>,
    max_length: &Bound<'_, PyAny>,
    suffix: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let name = name.cast_exact::<PyString>().ok()?;
    let text = name.to_str().ok()?;
    let max_length = max_length
        .cast_exact::<PyInt>()
        .ok()?
        .extract::<i64>()
        .ok()?;
    let length = i64::try_from(text.chars().count()).ok()?;
    if length <= max_length {
        return Some(name.clone());
    }

    let suffix = suffix.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let suffix_length = i64::try_from(suffix.chars().count()).ok()?;
    let stop = max_length.checked_sub(suffix_length)?;
    // Python's negative slice stop is relative to the original length.
    let stop = if stop < 0 {
        (length + stop).max(0)
    } else {
        stop
    };
    let cut = match text.char_indices().nth(usize::try_from(stop).ok()?) {
        Some((byte_index, _)) => byte_index,
        None => text.len(),
    };
    Some(PyString::new(py, &format!("{}{suffix}", &text[..cut])))
}

#[pyfunction]
pub(crate) fn prefix_rocketchat_team_display_name(name: &Bound<'_, PyAny>) -> Option<String> {
    let name = name.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("[TEAM] {name}"))
}
