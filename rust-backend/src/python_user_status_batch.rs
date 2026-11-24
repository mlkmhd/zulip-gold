use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString, PyTuple};

use crate::user_status::UserStatus;

#[pyfunction]
pub(crate) fn format_visible_user_statuses<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Ok(rows) = rows.cast_exact::<PyList>() else {
        return Ok(None);
    };
    let output = PyDict::new(py);
    let away_key = PyString::intern(py, "away");
    let text_key = PyString::intern(py, "status_text");
    let name_key = PyString::intern(py, "emoji_name");
    let code_key = PyString::intern(py, "emoji_code");
    let type_key = PyString::intern(py, "reaction_type");
    let true_value = true.into_pyobject(py).unwrap();
    for row in rows.iter() {
        let Some((id, enabled, text, name, code, kind)) = ordinary_status(&row) else {
            return Ok(None);
        };
        let (Ok(text_str), Ok(name_str), Ok(code_str), Ok(kind_str)) =
            (text.to_str(), name.to_str(), code.to_str(), kind.to_str())
        else {
            return Ok(None);
        };
        let fields = UserStatus::from_row(enabled, text_str, name_str, code_str, kind_str);
        let result = PyDict::new(py);
        if fields.away {
            result.set_item(&away_key, true_value)?;
        }
        if fields.text.is_some() {
            result.set_item(&text_key, &text)?;
        }
        if fields.emoji.is_some() {
            result.set_item(&name_key, &name)?;
            result.set_item(&code_key, &code)?;
            result.set_item(&type_key, &kind)?;
        }
        output.set_item(id.to_string(), result)?;
    }
    Ok(Some(output))
}

type Row<'py> = (
    i32,
    bool,
    Bound<'py, PyString>,
    Bound<'py, PyString>,
    Bound<'py, PyString>,
    Bound<'py, PyString>,
);

fn ordinary_status<'py>(row: &Bound<'py, PyAny>) -> Option<Row<'py>> {
    let row = row.cast_exact::<PyTuple>().ok()?;
    if row.len() != 6 {
        return None;
    }
    let id = row.get_item(0).ok()?;
    let id = id.cast_exact::<PyInt>().ok()?.extract().ok()?;
    let enabled = row.get_item(1).ok()?;
    let enabled = enabled.cast_exact::<PyBool>().ok()?.is_true();
    let string = |index| row.get_item(index).ok()?.cast_into_exact::<PyString>().ok();
    Some((id, enabled, string(2)?, string(3)?, string(4)?, string(5)?))
}

/// Update only the ordinary status row that Django has already selected.
/// `Some(false)` tells Django to remove the empty row from its own map.
#[pyfunction]
pub(crate) fn apply_user_status_row_event(
    py: Python<'_>,
    row: &Bound<'_, PyAny>,
    event: &Bound<'_, PyAny>,
) -> PyResult<Option<bool>> {
    let (Ok(row), Ok(event)) = (row.cast_exact::<PyDict>(), event.cast_exact::<PyDict>()) else {
        return Ok(None);
    };
    if row.is(event)
        || !event
            .iter()
            .all(|(key, _)| key.is_exact_instance_of::<PyString>())
    {
        return Ok(None);
    }
    let away_key = pyo3::intern!(py, "away");
    let text_key = pyo3::intern!(py, "status_text");
    let name_key = pyo3::intern!(py, "emoji_name");
    let code_key = pyo3::intern!(py, "emoji_code");
    let type_key = pyo3::intern!(py, "reaction_type");
    let kind_value = pyo3::intern!(py, "unicode_emoji");
    for (key, value) in row.iter() {
        let Ok(key) = key.cast_exact::<PyString>() else {
            return Ok(None);
        };
        match key.to_str() {
            Ok("away") if value.is_exact_instance_of::<PyBool>() => {}
            Ok("status_text" | "emoji_name" | "emoji_code" | "reaction_type")
                if value.is_exact_instance_of::<PyString>() => {}
            _ => return Ok(None),
        }
    }
    let mut edits = [const { None }; 5];
    if let Some(value) = event.get_item(away_key)?.filter(|value| !value.is_none()) {
        let Ok(value) = value.cast_into_exact::<PyBool>() else {
            return Ok(None);
        };
        let keep = value.is_true();
        edits[0] = Some((away_key, keep.then(|| value.into_any())));
    }
    if let Some(value) = event.get_item(text_key)?.filter(|value| !value.is_none()) {
        let Ok(value) = value.cast_into_exact::<PyString>() else {
            return Ok(None);
        };
        let keep = !value.is_empty()?;
        edits[1] = Some((text_key, keep.then(|| value.into_any())));
        if let Some(value) = event.get_item(name_key)?.filter(|value| !value.is_none()) {
            let Ok(value) = value.cast_into_exact::<PyString>() else {
                return Ok(None);
            };
            let has_emoji = !value.is_empty()?;
            edits[2] = Some((name_key, has_emoji.then(|| value.into_any())));
            if let Some(value) = event.get_item(code_key)?.filter(|value| !value.is_none()) {
                let Ok(value) = value.cast_into_exact::<PyString>() else {
                    return Ok(None);
                };
                let keep = !value.is_empty()?;
                edits[3] = Some((code_key, keep.then(|| value.into_any())));
            }
            if let Some(value) = event.get_item(type_key)?.filter(|value| !value.is_none()) {
                let Ok(value) = value.cast_into_exact::<PyString>() else {
                    return Ok(None);
                };
                let keep = has_emoji || !value.as_any().eq(kind_value)?;
                edits[4] = Some((type_key, keep.then(|| value.into_any())));
            }
        }
    }
    // No selected state is changed until every effective edit is validated.
    for (key, value) in edits.into_iter().flatten() {
        if let Some(value) = value {
            row.set_item(key, value)?;
        } else if row.contains(key)? {
            row.del_item(key)?;
        }
    }
    Ok(Some(!row.is_empty()))
}
