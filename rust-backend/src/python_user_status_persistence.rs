//! Update a Django-authorized existing status inside its caller transaction.

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyString, PyTuple};

#[pyfunction]
#[pyo3(signature = (cursor, user_id, client_id, edited_at, changes))]
pub(crate) fn update_existing_user_status<'py>(
    py: Python<'py>,
    cursor: &Bound<'py, PyAny>,
    user_id: i32,
    client_id: i32,
    edited_at: &Bound<'py, PyAny>,
    changes: (Option<&str>, Option<&str>, Option<&str>, Option<&str>),
) -> PyResult<bool> {
    update_status(py, cursor, user_id, client_id, edited_at, changes, true)
}

#[pyfunction]
#[pyo3(signature = (cursor, user_id, client_id, edited_at, changes))]
pub(crate) fn update_prepared_user_status<'py>(
    py: Python<'py>,
    cursor: &Bound<'py, PyAny>,
    user_id: i32,
    client_id: i32,
    edited_at: &Bound<'py, PyAny>,
    changes: (Option<&str>, Option<&str>, Option<&str>, Option<&str>),
) -> PyResult<bool> {
    update_status(py, cursor, user_id, client_id, edited_at, changes, false)
}

fn update_status<'py>(
    py: Python<'py>,
    cursor: &Bound<'py, PyAny>,
    user_id: i32,
    client_id: i32,
    edited_at: &Bound<'py, PyAny>,
    changes: (Option<&str>, Option<&str>, Option<&str>, Option<&str>),
    legacy_schema: bool,
) -> PyResult<bool> {
    let mut query = if legacy_schema {
        "UPDATE zulip.zerver_userstatus SET client_id = %s, timestamp = %s"
    } else {
        // Django resolves its quoted model table through the same connection.
        r#"UPDATE "zerver_userstatus" SET client_id = %s, timestamp = %s"#
    }
    .to_owned();
    let mut values = vec![
        client_id.into_pyobject(py)?.into_any().unbind(),
        edited_at.clone().unbind(),
    ];
    // Identifiers are fixed here; only values cross the SQL boundary.
    // Omitted fields must not even appear in the SET list.
    for (column, value) in [
        ("status_text", changes.0),
        ("emoji_name", changes.1),
        ("emoji_code", changes.2),
        ("reaction_type", changes.3),
    ] {
        if let Some(value) = value {
            query.push_str(", ");
            query.push_str(column);
            query.push_str(" = %s");
            values.push(PyString::new(py, value).into_any().unbind());
        }
    }
    query.push_str(" WHERE user_profile_id = %s");
    values.push(user_id.into_pyobject(py)?.into_any().unbind());
    cursor.call_method1("execute", (query, PyTuple::new(py, values)?))?;
    match cursor.getattr("rowcount")?.extract::<i64>()? {
        0 => Ok(false),
        1 => Ok(true),
        _ => Err(PyRuntimeError::new_err(
            "unexpected user status update count",
        )),
    }
}
