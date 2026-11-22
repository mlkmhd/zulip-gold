use std::cmp::Reverse;
use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFrozenSet, PyInt, PyList, PyString, PyTuple};

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
    let mut others = HashMap::<i64, Vec<i64>>::new();
    let mut pinned = HashSet::new();
    for row in subscriptions.iter() {
        let Ok(row) = row.cast_exact::<PyTuple>() else {
            return Ok(None);
        };
        if row.len() != 3 {
            return Ok(None);
        }
        let (Some(recipient), Some(subscriber), Some(pin)) = (
            integer(&row.get_item(0)?),
            integer(&row.get_item(1)?),
            boolean(&row.get_item(2)?),
        ) else {
            return Ok(None);
        };
        if subscriber != me {
            others.entry(recipient).or_default().push(subscriber);
        } else if pin {
            pinned.insert(recipient);
        }
    }
    let result = PyDict::new(py);
    for (recipient, latest) in recent.iter() {
        let (Some(recipient), Some(latest)) = (integer(&recipient), integer(&latest)) else {
            return Ok(None);
        };
        let participants = others
            .get(&recipient)
            .map(Vec::as_slice)
            .unwrap_or_default();
        let key = PyFrozenSet::new(py, participants)?;
        let row = PyDict::new(py);
        row.set_item("max_message_id", latest)?;
        row.set_item("pinned", pinned.contains(&recipient))?;
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
        let (Some(latest), Some(pinned)) =
            (row.get_item("max_message_id")?, row.get_item("pinned")?)
        else {
            return Ok(None);
        };
        let (Some(latest), Some(pinned), Some(mut ids)) = (
            integer(&latest),
            boolean(&pinned),
            participants
                .iter()
                .map(|id| integer(&id))
                .collect::<Option<Vec<_>>>(),
        ) else {
            return Ok(None);
        };
        ids.sort_unstable();
        rows.push((latest, pinned, ids));
    }
    // Ties retain the dictionary order, including updates to existing
    // conversation keys. Reverse avoids negating the smallest signed ID.
    rows.sort_by_key(|(latest, _, _)| Reverse(*latest));
    let result = PyList::empty(py);
    for (latest, pinned, ids) in rows {
        let row = PyDict::new(py);
        row.set_item("user_ids", ids)?;
        row.set_item("max_message_id", latest)?;
        row.set_item("pinned", pinned)?;
        result.append(row)?;
    }
    Ok(Some(result))
}
