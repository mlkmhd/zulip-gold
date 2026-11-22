//! Assemble only the directory fields Django selected for its caller.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString, PyTuple};

#[derive(Clone, Copy)]
enum ValueType {
    Text,
    Boolean,
    Integer,
    NullableText,
}

const BASE_FIELDS: [(&str, ValueType); 15] = [
    ("email", ValueType::Text),
    ("user_id", ValueType::Integer),
    ("avatar_source", ValueType::Text),
    ("avatar_version", ValueType::Integer),
    ("is_admin", ValueType::Boolean),
    ("is_owner", ValueType::Boolean),
    ("is_guest", ValueType::Boolean),
    ("role", ValueType::Integer),
    ("is_bot", ValueType::Boolean),
    ("full_name", ValueType::Text),
    ("timezone", ValueType::NullableText),
    ("is_active", ValueType::Boolean),
    ("date_joined", ValueType::Text),
    ("delivery_email", ValueType::NullableText),
    ("is_imported_stub", ValueType::Boolean),
];

fn matches_type(value: &Bound<'_, PyAny>, field: ValueType) -> bool {
    match field {
        ValueType::Text => value.is_exact_instance_of::<PyString>(),
        ValueType::Boolean => value.is_exact_instance_of::<PyBool>(),
        ValueType::Integer => value.is_exact_instance_of::<PyInt>(),
        ValueType::NullableText => value.is_none() || value.is_exact_instance_of::<PyString>(),
    }
}

fn project<'py>(
    py: Python<'py>,
    row: &Bound<'py, PyAny>,
) -> Option<(Bound<'py, PyAny>, Bound<'py, PyDict>)> {
    // The normal caller uses a NamedTuple; only read its stored fields.
    let facts = row.cast::<PyTuple>().ok()?;
    if facts.len() != BASE_FIELDS.len() + 4 {
        return None;
    }
    let result = PyDict::new(py);
    for (position, (key, field_type)) in BASE_FIELDS.into_iter().enumerate() {
        let value = facts.get_item(position).ok()?;
        if !matches_type(&value, field_type) {
            return None;
        }
        if key != "timezone" || !value.is_none() {
            result.set_item(key, value).ok()?;
        }
    }
    let is_deleted = facts.get_item(15).ok()?;
    if is_deleted.cast_exact::<PyBool>().ok()?.is_true() {
        result.set_item("is_deleted", is_deleted).ok()?;
    }
    let avatar = facts.get_item(16).ok()?;
    if !avatar.is_none() {
        let avatar = avatar.cast_exact::<PyTuple>().ok()?;
        if avatar.len() != 1 {
            return None;
        }
        let value = avatar.get_item(0).ok()?;
        if !matches_type(&value, ValueType::NullableText) {
            return None;
        }
        result.set_item("avatar_url", value).ok()?;
    }
    let bot = facts.get_item(17).ok()?;
    let profile = facts.get_item(18).ok()?;
    if !bot.is_none() {
        let bot = bot.cast_exact::<PyTuple>().ok()?;
        if bot.len() != 3 || !profile.is_none() {
            return None;
        }
        let kind = bot.get_item(0).ok()?;
        let system = bot.get_item(1).ok()?;
        let owner = bot.get_item(2).ok()?;
        if [&kind, &owner]
            .into_iter()
            .any(|value| !value.is_none() && !matches_type(value, ValueType::Integer))
        {
            return None;
        }
        result.set_item("bot_type", kind).ok()?;
        if system.cast_exact::<PyBool>().ok()?.is_true() {
            result.set_item("is_system_bot", system).ok()?;
        }
        result.set_item("bot_owner_id", owner).ok()?;
    } else if !profile.is_none() {
        result
            .set_item("profile_data", profile.cast_exact::<PyDict>().ok()?)
            .ok()?;
    }
    Some((facts.get_item(1).ok()?, result))
}

#[pyfunction]
pub(crate) fn assemble_user_directory<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyDict>> {
    let result = PyDict::new(py);
    for row in rows.cast_exact::<PyList>().ok()?.iter() {
        let (id, card) = project(py, &row)?;
        result.set_item(id, card).ok()?;
    }
    Some(result)
}
