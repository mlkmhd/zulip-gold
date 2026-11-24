use std::cmp::Reverse;
use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFrozenSet, PyInt, PyList, PyString, PyTuple};

use crate::python_schedule_replay::{fields, int_field};

fn integer(value: &Bound<'_, PyAny>) -> Option<i64> {
    value.cast_exact::<PyInt>().ok()?.extract().ok()
}

fn boolean(value: &Bound<'_, PyAny>) -> Option<bool> {
    value.cast_exact::<PyBool>().ok()?.extract().ok()
}

#[pyfunction]
pub(crate) fn assemble_recent_conversations<'py>(
    py: Python<'py>,
    me: &Bound<'py, PyAny>,
    recent: &Bound<'py, PyAny>,
    subscriptions: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let (Some(me), Ok(recent), Ok(subscriptions)) = (
        integer(me),
        recent.cast_exact::<PyDict>(),
        subscriptions.cast_exact::<PyList>(),
    ) else {
        return Ok(None);
    };
    #[derive(Default)]
    struct Recipient<'py> {
        participants: Vec<Bound<'py, PyAny>>,
        pinned: bool,
    }
    let mut recipients = HashMap::<i64, Recipient<'py>>::with_capacity(recent.len());
    for row in subscriptions.iter() {
        let Ok(row) = row.cast_exact::<PyTuple>() else {
            return Ok(None);
        };
        if row.len() != 3 {
            return Ok(None);
        }
        let subscriber = row.get_item(1)?;
        let (Some(recipient), Some(subscriber_id), Some(pin)) = (
            integer(&row.get_item(0)?),
            integer(&subscriber),
            boolean(&row.get_item(2)?),
        ) else {
            return Ok(None);
        };
        if subscriber_id != me {
            recipients
                .entry(recipient)
                .or_default()
                .participants
                .push(subscriber);
        } else if pin {
            recipients.entry(recipient).or_default().pinned = true;
        }
    }
    let result = PyDict::new(py);
    let latest_key = pyo3::intern!(py, "max_message_id");
    let pinned_key = pyo3::intern!(py, "pinned");
    for (recipient, latest) in recent.iter() {
        let Some(recipient) = integer(&recipient) else {
            return Ok(None);
        };
        if integer(&latest).is_none() {
            return Ok(None);
        }
        let (participants, pinned) = recipients
            .get(&recipient)
            .map(|recipient| (recipient.participants.as_slice(), recipient.pinned))
            .unwrap_or_default();
        let key = PyFrozenSet::new(py, participants)?;
        let row = PyDict::new(py);
        row.set_item(latest_key, latest)?;
        row.set_item(pinned_key, pinned)?;
        result.set_item(key, row)?;
    }
    Ok(Some(result))
}

#[pyfunction]
pub(crate) fn finalize_recent_conversations<'py>(
    py: Python<'py>,
    conversations: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    let Ok(conversations) = conversations.cast_exact::<PyDict>() else {
        return Ok(None);
    };
    let latest_key = pyo3::intern!(py, "max_message_id");
    let pinned_key = pyo3::intern!(py, "pinned");
    let ids_key = pyo3::intern!(py, "user_ids");
    let mut rows = Vec::with_capacity(conversations.len());
    for (participants, row) in conversations.iter() {
        let (Ok(participants), Ok(row)) = (
            participants.cast_exact::<PyFrozenSet>(),
            row.cast_exact::<PyDict>(),
        ) else {
            return Ok(None);
        };
        if !row
            .iter()
            .all(|(key, _)| key.is_exact_instance_of::<PyString>())
        {
            return Ok(None);
        }
        let (Some(latest), Some(pinned)) = (row.get_item(latest_key)?, row.get_item(pinned_key)?)
        else {
            return Ok(None);
        };
        let (Some(message_id), Some(pinned), Some(mut ids)) = (
            integer(&latest),
            boolean(&pinned),
            participants
                .iter()
                .map(|id| Some((integer(&id)?, id)))
                .collect::<Option<Vec<_>>>(),
        ) else {
            return Ok(None);
        };
        ids.sort_unstable_by_key(|(id, _)| *id);
        rows.push((message_id, latest, pinned, ids));
    }
    // Ties retain the dictionary order, including updates to existing
    // conversation keys. Reverse avoids negating the smallest signed ID.
    rows.sort_by_key(|(message_id, _, _, _)| Reverse(*message_id));
    let result = PyList::empty(py);
    for (_, latest, pinned, ids) in rows {
        let row = PyDict::new(py);
        row.set_item(ids_key, PyList::new(py, ids.into_iter().map(|(_, id)| id))?)?;
        row.set_item(latest_key, latest)?;
        row.set_item(pinned_key, pinned)?;
        result.append(row)?;
    }
    Ok(Some(result))
}

#[pyfunction]
pub(crate) fn replay_recent_conversation_message<'py>(
    py: Python<'py>,
    conversations: &Bound<'py, PyAny>,
    me: &Bound<'py, PyAny>,
    latest: &Bound<'py, PyAny>,
    recipients: &Bound<'py, PyAny>,
) -> PyResult<bool> {
    let (Ok(conversations), Some(me), Some(latest), Ok(recipients)) = (
        conversations.cast_exact::<PyDict>(),
        integer(me),
        integer(latest),
        recipients.cast_exact::<PyList>(),
    ) else {
        return Ok(false);
    };
    let mut participants = Vec::with_capacity(recipients.len());
    for recipient in recipients.iter() {
        let Some(id) = int_field(&recipient, "id") else {
            return Ok(false);
        };
        // The display recipient may contain other fields. Only the
        // identifier is read, and self never forms part of the key.
        if id != me {
            participants.push(id);
        }
    }
    let key = PyFrozenSet::new(py, participants)?;
    let pinned = if let Some(existing) = conversations.get_item(&key)? {
        if existing.is_none() {
            false
        } else {
            let Some(existing) = fields(&existing) else {
                return Ok(false);
            };
            let Some(pinned) = existing.get_item("pinned")? else {
                return Ok(false);
            };
            let Some(pinned) = boolean(&pinned) else {
                return Ok(false);
            };
            pinned
        }
    } else {
        false
    };

    // Assigning an existing dict key keeps its position, including
    // when that conversation's latest ID is lower than before.
    let row = PyDict::new(py);
    row.set_item("max_message_id", latest)?;
    row.set_item("pinned", pinned)?;
    conversations.set_item(&key, row)?;
    Ok(true)
}

#[pyfunction]
pub(crate) fn replay_recent_conversation_pin<'py>(
    py: Python<'py>,
    conversations: &Bound<'py, PyAny>,
    user_ids: &Bound<'py, PyAny>,
    pinned: &Bound<'py, PyAny>,
) -> PyResult<bool> {
    let (Ok(conversations), Ok(user_ids), Some(pinned)) = (
        conversations.cast_exact::<PyDict>(),
        user_ids.cast_exact::<PyList>(),
        boolean(pinned),
    ) else {
        return Ok(false);
    };
    let Some(participants) = user_ids
        .iter()
        .map(|id| integer(&id))
        .collect::<Option<Vec<_>>>()
    else {
        return Ok(false);
    };
    let key = PyFrozenSet::new(py, participants)?;
    if let Some(row) = conversations.get_item(&key)? {
        let Some(row) = fields(&row) else {
            return Ok(false);
        };
        // A pin for a conversation outside the register snapshot is a
        // no-op. When present, update this exact row rather than replace it.
        row.set_item("pinned", pinned)?;
    }
    Ok(true)
}
