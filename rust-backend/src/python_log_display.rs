//! Two literal presentation steps over data already prepared by Python.

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyString};

#[pyfunction]
pub(crate) fn log_level_display_prefix<'py>(
    level_name: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let level_name = level_name.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(level_name)?;
    // Each non-continuation byte starts one Python code point, including
    // a lone surrogate encoded with surrogatepass.
    match text
        .iter()
        .enumerate()
        .filter(|(_, byte)| **byte & 0xc0 != 0x80)
        .nth(4)
    {
        Some((end, _)) => python_string_from_bytes(level_name.py(), &text[..end]),
        None => Some(level_name.clone()),
    }
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
