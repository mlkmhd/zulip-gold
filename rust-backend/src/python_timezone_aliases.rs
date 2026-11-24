//! Pure processing of previously supplied timezone alias text and maps.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};

fn py_space(ch: char) -> bool {
    matches!(
        ch,
        '\u{9}'..='\u{d}' | '\u{1c}'..='\u{20}' | '\u{85}' | '\u{a0}' | '\u{1680}'
            | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}'
            | '\u{3000}'
    )
}

#[pyfunction]
pub(crate) fn parse_timezone_alias_lines<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Ok(lines) = value.cast_exact::<PyList>() else {
        return Ok(None);
    };
    let result = PyDict::new(py);
    for line in lines.iter() {
        let Ok(line) = line.cast_exact::<PyString>() else {
            return Ok(None);
        };
        let Ok(line) = line.to_str() else {
            return Ok(None);
        };
        let mut fields = line.split(py_space).filter(|field| !field.is_empty());
        let Some(code) = fields.next() else {
            continue;
        };
        // Python uses .lower(), not casefold() or ASCII-only comparison:
        // the Kelvin sign (K) at the end of a full "Link" is accepted.
        if "link".starts_with(&code.to_lowercase()) {
            let (Some(name), Some(alias), None) = (fields.next(), fields.next(), fields.next())
            else {
                return Ok(None);
            };
            result.set_item(alias, name)?;
        }
    }
    Ok(Some(result))
}

#[pyfunction]
pub(crate) fn lookup_timezone_alias<'py>(
    aliases: &Bound<'py, PyAny>,
    key: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyString>>> {
    let (Ok(aliases), Ok(key)) = (aliases.cast_exact::<PyDict>(), key.cast_exact::<PyString>())
    else {
        return Ok(None);
    };
    // Never invoke arbitrary key comparison on declined Python containers.
    if aliases
        .iter()
        .any(|(candidate, _)| candidate.cast_exact::<PyString>().is_err())
    {
        return Ok(None);
    }
    match aliases.get_item(key)? {
        Some(value) => Ok(value.cast_into_exact::<PyString>().ok()),
        None => Ok(Some(key.clone())),
    }
}
