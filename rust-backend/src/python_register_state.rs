use std::collections::HashSet;

use pyo3::exceptions::PyKeyError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString};

fn plain_row<'a, 'py>(value: &'a Bound<'py, PyAny>) -> Option<&'a Bound<'py, PyDict>> {
    let row = value.cast_exact::<PyDict>().ok()?;
    row.iter()
        .all(|(key, _)| key.is_exact_instance_of::<PyString>())
        .then_some(row)
}

type UserPartitions<'py> = (Bound<'py, PyList>, Bound<'py, PyList>);

#[pyfunction]
pub(crate) fn partition_register_users<'py>(
    py: Python<'py>,
    raw: &Bound<'py, PyAny>,
) -> PyResult<Option<UserPartitions<'py>>> {
    let Ok(raw) = raw.cast_exact::<PyDict>() else {
        return Ok(None);
    };
    let mut rows = Vec::with_capacity(raw.len());
    let mut identities = HashSet::with_capacity(raw.len());
    for value in raw.values().iter() {
        let Some(row) = plain_row(&value) else {
            return Ok(None);
        };
        if !identities.insert(row.as_ptr()) {
            // Python pops the field once per entry, even for aliased rows.
            return Ok(None);
        }
        let (Some(id), Some(active)) = (row.get_item("user_id")?, row.get_item("is_active")?)
        else {
            return Ok(None);
        };
        let (Some(id), Some(active)) = (
            id.cast_exact::<PyInt>()
                .ok()
                .and_then(|id| id.extract::<i64>().ok()),
            active
                .cast_exact::<PyBool>()
                .ok()
                .and_then(|active| active.extract::<bool>().ok()),
        ) else {
            return Ok(None);
        };
        rows.push((id, active, row.clone()));
    }
    rows.sort_by_key(|(id, _, _)| *id);
    let active_users = PyList::empty(py);
    let inactive_users = PyList::empty(py);
    for (_, active, row) in &rows {
        if *active {
            active_users.append(row)?;
        } else {
            inactive_users.append(row)?;
        }
    }
    for (_, _, row) in rows {
        row.del_item("is_active")?;
    }
    Ok(Some((active_users, inactive_users)))
}

const NOTIFICATIONS: [(&str, &str); 4] = [
    (
        "desktop_notifications",
        "enable_stream_desktop_notifications",
    ),
    (
        "audible_notifications",
        "enable_stream_audible_notifications",
    ),
    ("push_notifications", "enable_stream_push_notifications"),
    ("email_notifications", "enable_stream_email_notifications"),
];

#[pyfunction]
pub(crate) fn fill_legacy_register_notifications(
    subscribed: &Bound<'_, PyAny>,
    unsubscribed: &Bound<'_, PyAny>,
    profile: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let (Ok(subscribed), Ok(unsubscribed)) = (
        subscribed.cast_exact::<PyList>(),
        unsubscribed.cast_exact::<PyList>(),
    ) else {
        return Ok(false);
    };
    let mut rows = Vec::with_capacity(subscribed.len() + unsubscribed.len());
    for value in subscribed.iter().chain(unsubscribed.iter()) {
        let Some(row) = plain_row(&value) else {
            return Ok(false);
        };
        for (field, _) in NOTIFICATIONS {
            if !row
                .get_item(field)?
                .is_some_and(|value| value.is_none() || value.is_exact_instance_of::<PyBool>())
            {
                return Ok(false);
            }
        }
        rows.push(row.clone());
    }
    for row in rows {
        for (field, fallback) in NOTIFICATIONS {
            let value = row
                .get_item(field)?
                .ok_or_else(|| PyKeyError::new_err(field))?;
            if value.is_none() {
                if profile.is_none() {
                    row.set_item(field, false)?;
                } else {
                    row.set_item(field, profile.getattr(fallback)?)?;
                }
            }
        }
    }
    Ok(true)
}

#[pyfunction]
pub(crate) fn fill_legacy_register_topics(
    topics: &Bound<'_, PyAny>,
    fallback: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let (Ok(topics), Ok(fallback)) = (
        topics.cast_exact::<PyList>(),
        fallback.cast_exact::<PyString>(),
    ) else {
        return Ok(false);
    };
    let mut empty = Vec::new();
    for value in topics.iter() {
        let Some(row) = plain_row(&value) else {
            return Ok(false);
        };
        let Some(topic) = row.get_item("topic_name")? else {
            return Ok(false);
        };
        let Ok(topic) = topic.cast_exact::<PyString>() else {
            return Ok(false);
        };
        if topic.is_empty()? {
            empty.push(row.clone());
        }
    }
    for row in empty {
        row.set_item("topic_name", fallback)?;
    }
    Ok(true)
}
