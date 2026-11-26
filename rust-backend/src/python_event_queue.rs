use std::collections::BTreeMap;

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString};

fn plain_event<'a, 'py>(value: &'a Bound<'py, PyAny>) -> Option<&'a Bound<'py, PyDict>> {
    let event = value.cast_exact::<PyDict>().ok()?;
    event
        .iter()
        .all(|(key, _)| key.is_exact_instance_of::<PyString>())
        .then_some(event)
}

#[pyfunction]
pub(crate) fn classify_queued_event<'py>(
    py: Python<'py>,
    event: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyString>>> {
    let Some(event) = plain_event(event) else {
        return Ok(None);
    };
    let Some(kind) = event.get_item("type")? else {
        return Ok(None);
    };
    let Ok(kind) = kind.cast_into_exact::<PyString>() else {
        return Ok(None);
    };
    if !kind.eq("update_message_flags") {
        return Ok(Some(kind));
    }
    let (Some(all), Some(flag), Some(op)) = (
        event.get_item("all")?,
        event.get_item("flag")?,
        event.get_item("operation")?,
    ) else {
        return Ok(None);
    };
    let (Ok(all), Ok(flag), Ok(op)) = (
        all.cast_exact::<PyBool>(),
        flag.cast_exact::<PyString>(),
        op.cast_exact::<PyString>(),
    ) else {
        return Ok(None);
    };
    let (Ok(flag), Ok(op)) = (flag.to_str(), op.to_str()) else {
        return Ok(None);
    };
    let key = if all.extract::<bool>()? {
        format!("all_flags/{flag}/{op}")
    } else {
        format!("flags/{op}/{flag}")
    };
    Ok(Some(PyString::new(py, &key)))
}

#[pyfunction]
pub(crate) fn fold_queued_flag_event(
    previous: &Bound<'_, PyAny>,
    current: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let (Some(previous), Some(current)) = (plain_event(previous), plain_event(current)) else {
        return Ok(false);
    };
    let (Some(id), Some(existing), Some(additional)) = (
        current.get_item("id")?,
        previous.get_item("messages")?,
        current.get_item("messages")?,
    ) else {
        return Ok(false);
    };
    if !id.is_exact_instance_of::<PyInt>() {
        return Ok(false);
    }
    let (Ok(existing), Ok(additional)) = (
        existing.cast_exact::<PyList>(),
        additional.cast_exact::<PyList>(),
    ) else {
        return Ok(false);
    };

    previous.set_item("id", id)?;
    existing.call_method1("extend", (additional,))?;
    if let Some(timestamp) = current.get_item("timestamp")? {
        previous.set_item("timestamp", timestamp)?;
    }
    Ok(true)
}

fn safe_event_id(event: &Bound<'_, PyAny>) -> Option<i64> {
    let id = plain_event(event)?.get_item("id").ok()??;
    id.cast_exact::<PyInt>().ok()?.extract().ok()
}

#[pyfunction]
pub(crate) fn merge_queued_events<'py>(
    py: Python<'py>,
    queue: &Bound<'py, PyAny>,
    deque_type: &Bound<'py, PyAny>,
    virtual_events: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    let Ok(virtual_events) = virtual_events.cast_exact::<PyDict>() else {
        return Ok(None);
    };
    if !queue.get_type().is(deque_type) {
        return Ok(None);
    }

    let mut by_id = BTreeMap::new();
    for value in virtual_events.values().iter() {
        let Some(id) = safe_event_id(&value) else {
            return Ok(None);
        };
        by_id.insert(id, value);
    }

    let output = PyList::empty(py);
    let mut pending = by_id.into_iter().peekable();
    for event in queue.try_iter()? {
        let event = event?;
        let Some(id) = safe_event_id(&event) else {
            return Ok(None);
        };
        // Preserve the original queue order, even for restored queues
        // where IDs coincide. A real event goes before a virtual tie.
        while pending
            .peek()
            .is_some_and(|(virtual_id, _)| *virtual_id < id)
        {
            if let Some((_, value)) = pending.next() {
                output.append(value)?;
            }
        }
        output.append(event)?;
    }
    for (_, value) in pending {
        output.append(value)?;
    }
    Ok(Some(output))
}

/// Only standard virtual-event keys use this by default. Other keys can
/// have custom mapping lookup behavior in the Python reference.
#[pyfunction]
pub(crate) fn merge_prepared_queued_events<'py>(
    py: Python<'py>,
    queue: &Bound<'py, PyAny>,
    deque_type: &Bound<'py, PyAny>,
    virtual_events: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    if plain_event(virtual_events).is_none() {
        return Ok(None);
    }
    merge_queued_events(py, queue, deque_type, virtual_events)
}

#[pyfunction]
pub(crate) fn plan_prepared_queue_retry(
    event: &Bound<'_, PyAny>,
    limit: &Bound<'_, PyAny>,
) -> Option<(i64, bool)> {
    let event = plain_event(event)?;
    let limit = limit.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let attempts = match event.get_item("failed_tries").ok()? {
        None => 0,
        Some(value) => value.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?,
    };
    let next_attempts = attempts.checked_add(1)?;
    Some((next_attempts, next_attempts > limit))
}
