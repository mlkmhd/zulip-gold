//! Literal Gong display text over already selected strings/counts.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyString};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

fn exact_i64(value: &Bound<'_, PyAny>) -> Option<i64> {
    value.cast_exact::<PyInt>().ok()?.extract().ok()
}

#[pyfunction]
pub(crate) fn format_gong_duration_display(duration: &Bound<'_, PyAny>) -> Option<String> {
    let duration = i128::from(exact_i64(duration)?);
    if duration < 60 {
        let word = if duration == 1 { "second" } else { "seconds" };
        return Some(format!("{duration} {word}"));
    }

    let total_minutes = (duration + 30) / 60;
    let hours = total_minutes / 60;
    let minutes = total_minutes % 60;
    let hour_word = if hours == 1 { "hour" } else { "hours" };
    let minute_word = if minutes == 1 { "minute" } else { "minutes" };
    Some(match (hours, minutes) {
        (0, _) => format!("{minutes} {minute_word}"),
        (_, 0) => format!("{hours} {hour_word}"),
        _ => format!("{hours} {hour_word} {minutes} {minute_word}"),
    })
}

#[pyfunction]
pub(crate) fn join_gong_topic_display<'py>(
    labels: &Bound<'py, PyAny>,
    extra_topics: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let labels = labels.cast_exact::<PyList>().ok()?;
    let extra_topics = exact_i64(extra_topics)?;
    let mut text = Vec::new();
    for (index, label) in labels.iter().enumerate() {
        if index != 0 {
            text.extend_from_slice(b", ");
        }
        text.extend_from_slice(&python_string_bytes(label.cast_exact::<PyString>().ok()?)?);
    }

    if extra_topics > 0 {
        let word = if extra_topics == 1 { "topic" } else { "topics" };
        text.extend_from_slice(format!(" and {extra_topics} more {word}").as_bytes());
    }
    if !text.is_empty() {
        text.push(b'.');
    }
    python_string_from_bytes(labels.py(), &text)
}
