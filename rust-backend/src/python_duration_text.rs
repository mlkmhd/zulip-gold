//! Human-readable text from an already measured scalar duration.

use pyo3::prelude::*;
use pyo3::types::PyFloat;

fn duration_text(seconds: f64) -> Option<String> {
    // Keep nonfinite and outlying values on Python's formatting path.
    if !seconds.is_finite() || seconds.abs() > 1_000_000.0 {
        return None;
    }
    Some(if seconds >= 1.0 {
        format!("{seconds:.1}s")
    } else {
        let milliseconds = seconds * 1000.0;
        format!("{milliseconds:.0}ms")
    })
}

#[pyfunction]
pub(crate) fn format_prepared_duration(value: &Bound<'_, PyAny>) -> Option<String> {
    let seconds = value.cast_exact::<PyFloat>().ok()?.extract::<f64>().ok()?;
    duration_text(seconds)
}

#[cfg(test)]
mod tests {
    use super::duration_text;

    #[test]
    fn keeps_display_units_rounding_and_negative_zero() {
        for (value, want) in [
            (0.0005, "0ms"),
            (0.0015, "2ms"),
            (-0.0, "-0ms"),
            (0.9999, "1000ms"),
            (1.0, "1.0s"),
            (1.25, "1.2s"),
        ] {
            assert_eq!(duration_text(value).as_deref(), Some(want));
        }
        assert_eq!(duration_text(f64::NAN), None);
        assert_eq!(duration_text(1_000_001.0), None);
    }
}
