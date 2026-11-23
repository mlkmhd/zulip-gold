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
