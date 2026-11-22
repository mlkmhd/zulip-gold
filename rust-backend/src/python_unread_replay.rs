//! Reconcile already selected unread read/unread, move and delete events.

use pyo3::PyTypeInfo;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PySet, PyString};

use crate::python_schedule_replay::{fields, int_field};

fn id_list(value: &Bound<'_, PyAny>) -> Option<Vec<i64>> {
    value
        .cast_exact::<PyList>()
        .ok()?
        .iter()
        .map(|id| id.cast_exact::<PyInt>().ok()?.extract().ok())
        .collect()
}

fn member<'py, T: PyTypeInfo>(raw: &Bound<'py, PyDict>, name: &str) -> Option<Bound<'py, T>> {
    raw.get_item(name).ok()??.cast_into_exact::<T>().ok()
}

struct State<'py> {
    pms: Bound<'py, PyDict>,
    streams: Bound<'py, PyDict>,
    huddles: Bound<'py, PyDict>,
    mentions: Bound<'py, PySet>,
    unmuted: Bound<'py, PySet>,
}

impl<'py> State<'py> {
    fn parse(value: &Bound<'py, PyAny>) -> Option<Self> {
        let raw = fields(value)?;
        Some(Self {
            pms: member(raw, "pm_dict")?,
            streams: member(raw, "stream_dict")?,
            huddles: member(raw, "huddle_dict")?,
            mentions: member(raw, "mentions")?,
            unmuted: member(raw, "unmuted_stream_msgs")?,
        })
    }
}

#[pyfunction]
pub(crate) fn remove_unread_messages(
    selected: &Bound<'_, PyAny>,
    values: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let (Some(state), Some(ids)) = (State::parse(selected), id_list(values)) else {
        return Ok(false);
    };
    for id in ids {
        for bucket in [&state.pms, &state.streams, &state.huddles] {
            if bucket.contains(id)? {
                bucket.del_item(id)?;
            }
        }
        state.unmuted.discard(id)?;
        state.mentions.discard(id)?;
    }
    Ok(true)
}

enum Recipient<'py> {
    Direct(i64),
    Group(String),
    Channel(i64, Bound<'py, PyString>, bool),
}

struct Restore<'py> {
    id: i64,
    mentioned: bool,
    recipient: Recipient<'py>,
}

fn parse_restore<'py>(my_id: i64, candidates: &Bound<'py, PyAny>) -> Option<Vec<Restore<'py>>> {
    let candidates = fields(candidates)?;
    candidates
        .iter()
        .map(|(key, details)| {
            let key = key.cast_exact::<PyString>().ok()?.to_str().ok()?;
            let id = key.parse::<i64>().ok()?;
            if id.to_string() != key {
                return None;
            }
            let details = fields(&details)?;
            let mentioned = match details.get_item("mentioned").ok()? {
                None => false,
                Some(value) if value.is_none() => false,
                Some(value) => value.cast_exact::<PyBool>().ok()?.is_true(),
            };
            let kind = member::<PyString>(details, "type")?;
            let recipient = match kind.to_str().ok()? {
                "private" => {
                    let mut users = id_list(&details.get_item("user_ids").ok()??)?;
                    users.retain(|user| *user != my_id);
                    match users.as_slice() {
                        [] => Recipient::Direct(my_id),
                        [user] => Recipient::Direct(*user),
                        _ => {
                            users.push(my_id);
                            users.sort_unstable();
                            Recipient::Group(
                                users
                                    .into_iter()
                                    .map(|user| user.to_string())
                                    .collect::<Vec<_>>()
                                    .join(","),
                            )
                        }
                    }
                }
                "stream" => Recipient::Channel(
                    int_field(details.as_any(), "stream_id")?,
                    member(details, "topic")?,
                    member::<PyBool>(details, "unmuted_stream_msg")?.is_true(),
                ),
                _ => return None,
            };
            Some(Restore {
                id,
                mentioned,
                recipient,
            })
        })
        .collect()
}

#[pyfunction]
pub(crate) fn restore_unread_messages<'py>(
    py: Python<'py>,
    selected: &Bound<'py, PyAny>,
    my_id: &Bound<'py, PyAny>,
    candidates: &Bound<'py, PyAny>,
) -> PyResult<bool> {
    let Some(state) = State::parse(selected) else {
        return Ok(false);
    };
    let Ok(my_id) = my_id.cast_exact::<PyInt>() else {
        return Ok(false);
    };
    let Ok(my_id) = my_id.extract::<i64>() else {
        return Ok(false);
    };
    let Some(changes) = parse_restore(my_id, candidates) else {
        return Ok(false);
    };

    // Preserve Python's dictionary key order and set update order.
    for change in changes {
        if change.mentioned {
            state.mentions.add(change.id)?;
        }
        let row = PyDict::new(py);
        match change.recipient {
            Recipient::Direct(other) => {
                row.set_item("other_user_id", other)?;
                state.pms.set_item(change.id, row)?;
            }
            Recipient::Group(users) => {
                row.set_item("user_ids_string", users)?;
                state.huddles.set_item(change.id, row)?;
            }
            Recipient::Channel(stream_id, topic, unmuted) => {
                row.set_item("stream_id", stream_id)?;
                row.set_item("topic", topic)?;
                state.streams.set_item(change.id, row)?;
                if unmuted {
                    state.unmuted.add(change.id)?;
                }
            }
        }
    }
    Ok(true)
}

#[pyfunction]
pub(crate) fn move_unread_messages<'py>(
    selected: &Bound<'py, PyAny>,
    values: &Bound<'py, PyAny>,
    stream: Option<&Bound<'py, PyAny>>,
    topic: Option<&Bound<'py, PyAny>>,
) -> PyResult<bool> {
    let (Some(state), Some(ids)) = (State::parse(selected), id_list(values)) else {
        return Ok(false);
    };
    let stream = match stream {
        None => None,
        Some(value) => match value.cast_exact::<PyInt>() {
            Ok(value) => Some(value),
            Err(_) => return Ok(false),
        },
    };
    let topic = match topic {
        None => None,
        Some(value) => match value.cast_exact::<PyString>() {
            Ok(value) => Some(value),
            Err(_) => return Ok(false),
        },
    };
    let mut rows = Vec::new();
    for id in ids {
        if let Some(row) = state.streams.get_item(id)? {
            let Some(row) = fields(&row) else {
                return Ok(false);
            };
            rows.push(row.clone());
        }
    }

    // The reference assigns the channel before the topic and does not
    // compute mute policy here. Leave all other buckets untouched.
    if let Some(stream) = stream {
        for row in &rows {
            row.set_item("stream_id", stream)?;
        }
    }
    if let Some(topic) = topic {
        for row in &rows {
            row.set_item("topic", topic)?;
        }
    }
    Ok(true)
}
