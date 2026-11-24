//! Two literal presentation steps over data already prepared by Python.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyString};

#[pyfunction]
pub(crate) fn log_level_display_prefix<'py>(
    level_name: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let level_name = level_name.cast_exact::<PyString>().ok()?;
    let text = level_name.to_str().ok()?;
    Some(match text.char_indices().nth(4) {
        Some((end, _)) => PyString::new(level_name.py(), &text[..end]),
        None => level_name.clone(),
    })
}

#[pyfunction]
pub(crate) fn standard_log_display_format(include_pid: &Bound<'_, PyAny>) -> Option<&'static str> {
    let include_pid = include_pid.cast_exact::<PyBool>().ok()?.is_true();

    Some(if include_pid {
        "%(asctime)s %(zulip_level_abbrev)-4s pid:%(process)d [%(zulip_origin)s] %(message)s"
    } else {
        "%(asctime)s %(zulip_level_abbrev)-4s [%(zulip_origin)s] %(message)s"
    })
}
