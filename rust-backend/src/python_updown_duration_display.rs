//! Display-only duration wording from already provided integer seconds.

use std::fmt::Write;

use pyo3::prelude::*;
use pyo3::types::PyInt;

#[pyfunction]
pub(crate) fn format_updown_duration_display(duration: &Bound<'_, PyAny>) -> Option<String> {
    let duration = duration.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let days = duration.div_euclid(86_400);
    let remainder = duration.rem_euclid(86_400);
    let hours = remainder / 3_600;
    let minutes = remainder % 3_600 / 60;
    let seconds = remainder % 60;

    let mut display = String::new();
    for (amount, unit) in [
        (days, "day"),
        (hours, "hour"),
        (minutes, "minute"),
        (seconds, "second"),
    ] {
        if amount > 0 {
            if !display.is_empty() {
                display.push(' ');
            }
            let suffix = if amount == 1 { "" } else { "s" };
            write!(&mut display, "{amount} {unit}{suffix}").ok()?;
        }
    }
    Some(display)
}
