//! Fixed display strings over components already prepared by Python.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyString};

#[pyfunction]
pub(crate) fn wrap_global_time_display(text: &Bound<'_, PyAny>) -> Option<String> {
    let text = text.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("<time:{text}>"))
}

#[pyfunction]
pub(crate) fn format_gmt_display_name(
    sign: &Bound<'_, PyAny>,
    hours: &Bound<'_, PyAny>,
    minutes: &Bound<'_, PyAny>,
) -> Option<String> {
    let sign = sign.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let hours = hours.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let minutes = minutes.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    if !matches!(sign, "+" | "-") || !(0..24).contains(&hours) || !(0..60).contains(&minutes) {
        return None;
    }
    Some(format!("GMT{sign}{hours:02}:{minutes:02}"))
}
