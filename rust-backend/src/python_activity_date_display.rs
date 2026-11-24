//! Fixed activity display labels for already prepared Python calendar fields.

use pyo3::prelude::*;
use pyo3::types::PyInt;

fn exact_component(value: &Bound<'_, PyAny>, min: u16, max: u16) -> Option<u16> {
    let value = value.cast_exact::<PyInt>().ok()?.extract::<u16>().ok()?;
    (min..=max).contains(&value).then_some(value)
}

fn date_components(
    year: &Bound<'_, PyAny>,
    month: &Bound<'_, PyAny>,
    day: &Bound<'_, PyAny>,
) -> Option<(u16, u16, u16)> {
    Some((
        exact_component(year, 1, 9999)?,
        exact_component(month, 1, 12)?,
        exact_component(day, 1, 31)?,
    ))
}

#[pyfunction]
pub(crate) fn format_activity_display_day(
    year: &Bound<'_, PyAny>,
    month: &Bound<'_, PyAny>,
    day: &Bound<'_, PyAny>,
) -> Option<String> {
    let (year, month, day) = date_components(year, month, day)?;
    Some(format!("{year:04}-{month:02}-{day:02}"))
}

#[pyfunction]
pub(crate) fn format_activity_display_minute(
    year: &Bound<'_, PyAny>,
    month: &Bound<'_, PyAny>,
    day: &Bound<'_, PyAny>,
    hour: &Bound<'_, PyAny>,
    minute: &Bound<'_, PyAny>,
) -> Option<String> {
    let (year, month, day) = date_components(year, month, day)?;
    let hour = exact_component(hour, 0, 23)?;
    let minute = exact_component(minute, 0, 59)?;
    Some(format!(
        "{year:04}-{month:02}-{day:02} {hour:02}:{minute:02}"
    ))
}
