//! Pure format operations on already selected topic names.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyInt, PyString};

use crate::python_text_bytes::python_string_bytes;

const TOPIC_SUFFIX: &str = "...";
const RESOLUTION_PREFIX: &str = "✔ ";

#[pyfunction]
pub(crate) fn truncate_topic_title<'py>(
    topic: &Bound<'py, PyAny>,
    max_length: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let topic = topic.cast_exact::<PyString>().ok()?;
    let max_length = max_length
        .cast_exact::<PyInt>()
        .ok()?
        .extract::<i64>()
        .ok()?;
    let value = topic.to_str().ok()?;
    let length = value.chars().count() as i128;
    let max_length = i128::from(max_length);
    if length <= max_length {
        return Some(topic.clone());
    }

    // Python's stop-only slice counts code points and treats negative
    // stops as offsets from the end.
    let stop = max_length - TOPIC_SUFFIX.len() as i128;
    let end = (if stop < 0 { length + stop } else { stop }).clamp(0, length) as usize;
    let byte = value
        .char_indices()
        .nth(end)
        .map_or(value.len(), |(byte, _)| byte);
    let mut rendered = String::with_capacity(byte + TOPIC_SUFFIX.len());
    rendered.push_str(&value[..byte]);
    rendered.push_str(TOPIC_SUFFIX);
    Some(PyString::new(topic.py(), &rendered))
}

#[pyfunction]
pub(crate) fn split_resolved_topic_title<'py>(
    topic: &Bound<'py, PyAny>,
) -> Option<(bool, Bound<'py, PyString>)> {
    let topic = topic.cast_exact::<PyString>().ok()?;
    let value = topic.to_str().ok()?;
    match value.strip_prefix(RESOLUTION_PREFIX) {
        Some(bare) => Some((true, PyString::new(topic.py(), bare))),
        None => Some((false, topic.clone())),
    }
}

#[pyfunction]
pub(crate) fn normalize_empty_topic_alias<'py>(
    topic: &Bound<'py, PyAny>,
    alias: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let topic = topic.cast_exact::<PyString>().ok()?;
    let alias = alias.cast_exact::<PyString>().ok()?;
    if python_string_bytes(topic)? == python_string_bytes(alias)? {
        Some(PyString::new(topic.py(), ""))
    } else {
        Some(topic.clone())
    }
}

#[pyfunction]
pub(crate) fn format_legacy_empty_topic<'py>(
    topic: &Bound<'py, PyAny>,
    is_channel_message: &Bound<'py, PyAny>,
    allow_empty_topic_name: &Bound<'py, PyAny>,
    fallback: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let topic = topic.cast_exact::<PyString>().ok()?;
    let fallback = fallback.cast_exact::<PyString>().ok()?;
    let is_channel_message = is_channel_message.cast_exact::<PyBool>().ok()?.is_true();
    let allow_empty_topic_name = allow_empty_topic_name
        .cast_exact::<PyBool>()
        .ok()?
        .is_true();
    if is_channel_message && python_string_bytes(topic)?.is_empty() && !allow_empty_topic_name {
        Some(fallback.clone())
    } else {
        Some(topic.clone())
    }
}
