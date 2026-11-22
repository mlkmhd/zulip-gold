//! Reconcile already selected scheduled-message and reminder events in memory.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString};

const DELIVERY: &str = "scheduled_delivery_timestamp";

#[derive(Clone, Copy)]
enum Family {
    Scheduled,
    Reminder,
}

impl Family {
    fn list_key(self) -> &'static str {
        match self {
            Self::Scheduled => "scheduled_messages",
            Self::Reminder => "reminders",
        }
    }

    fn id_key(self) -> &'static str {
        match self {
            Self::Scheduled => "scheduled_message_id",
            Self::Reminder => "reminder_id",
        }
    }
}

enum Change<'py> {
    Noop,
    Remove(usize),
    Replace(usize, Bound<'py, PyAny>),
    Reorder(Vec<Bound<'py, PyAny>>),
}

pub(crate) fn fields<'a, 'py>(value: &'a Bound<'py, PyAny>) -> Option<&'a Bound<'py, PyDict>> {
    let value = value.cast_exact::<PyDict>().ok()?;
    value
        .iter()
        .all(|(key, _)| key.is_exact_instance_of::<PyString>())
        .then_some(value)
}

pub(crate) fn int_field(value: &Bound<'_, PyAny>, key: &str) -> Option<i64> {
    let value = fields(value)?.get_item(key).ok()??;
    value.cast_exact::<PyInt>().ok()?.extract().ok()
}

fn sorted_rows<'py>(rows: impl IntoIterator<Item = Bound<'py, PyAny>>) -> Option<Change<'py>> {
    let mut keys = rows
        .into_iter()
        .map(|row| Some((int_field(&row, DELIVERY)?, row)))
        .collect::<Option<Vec<_>>>()?;
    keys.sort_by_key(|(timestamp, _)| *timestamp);
    Some(Change::Reorder(
        keys.into_iter().map(|(_, row)| row).collect(),
    ))
}

pub(crate) fn find_row(
    rows: &Bound<'_, PyList>,
    key: &str,
    id: i64,
    unique: bool,
) -> Option<Option<usize>> {
    let mut matched = None;
    for (index, row) in rows.iter().enumerate() {
        if int_field(&row, key)? == id {
            if !unique {
                return Some(Some(index));
            }
            if matched.replace(index).is_some() {
                // Let the reference handle old lists with repeated identifiers.
                return None;
            }
        }
    }
    Some(matched)
}

fn plan<'py>(
    rows: &Bound<'py, PyList>,
    event: &Bound<'py, PyDict>,
    family: Family,
    operation: &str,
) -> Option<Change<'py>> {
    match operation {
        "add" => {
            let value = event.get_item(family.list_key()).ok()??;
            let addition = value.cast_exact::<PyList>().ok()?;
            if addition.len() != 1 {
                return None;
            }
            sorted_rows(rows.iter().chain([addition.get_item(0).ok()?]))
        }
        "update" if matches!(family, Family::Scheduled) => {
            let replacement = event.get_item("scheduled_message").ok()??;
            let key = family.id_key();
            let id = int_field(&replacement, key)?;
            let Some(index) = find_row(rows, key, id, false)? else {
                return Some(Change::Noop);
            };
            if int_field(&rows.get_item(index).ok()?, DELIVERY)?
                == int_field(&replacement, DELIVERY)?
            {
                return Some(Change::Replace(index, replacement));
            }
            sorted_rows(rows.iter().enumerate().map(|(i, row)| {
                if i == index { replacement.clone() } else { row }
            }))
        }
        "remove" => {
            let key = family.id_key();
            let id = int_field(event.as_any(), key)?;
            Some(
                find_row(rows, key, id, matches!(family, Family::Scheduled))?
                    .map_or(Change::Noop, Change::Remove),
            )
        }
        _ => None,
    }
}

#[pyfunction]
pub(crate) fn apply_scheduled_list_event(
    py: Python<'_>,
    selected: &Bound<'_, PyAny>,
    event: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let Some(event) = fields(event) else {
        return Ok(false);
    };
    let (Some(kind), Some(operation)) = (event.get_item("type")?, event.get_item("op")?) else {
        return Ok(false);
    };
    let (Ok(kind), Ok(operation)) = (
        kind.cast_exact::<PyString>(),
        operation.cast_exact::<PyString>(),
    ) else {
        return Ok(false);
    };
    let family = match kind.to_str() {
        Ok("scheduled_messages") => Family::Scheduled,
        Ok("reminders") => Family::Reminder,
        _ => return Ok(false),
    };
    let (Ok(rows), Ok(operation)) = (selected.cast_exact::<PyList>(), operation.to_str()) else {
        return Ok(false);
    };
    let Some(change) = plan(rows, event, family, operation) else {
        return Ok(false);
    };

    // Prepare completely before changing the original list. Unaffected rows
    // and the selected event payload keep their Python object identities.
    match change {
        Change::Noop => {}
        Change::Remove(index) => rows.del_item(index)?,
        Change::Replace(index, value) => rows.set_item(index, value)?,
        Change::Reorder(values) => {
            rows.set_slice(0, rows.len(), PyList::new(py, values)?.as_any())?
        }
    }
    Ok(true)
}
