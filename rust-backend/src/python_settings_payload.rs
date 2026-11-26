use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString, PyTuple};

#[pyfunction]
pub(crate) fn snapshot_settings_properties<'py>(
    py: Python<'py>,
    source: &Bound<'py, PyAny>,
    schema: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Ok(schema) = schema.cast_exact::<PyDict>() else {
        return Ok(None);
    };
    let names = schema.keys();
    if !names
        .iter()
        .all(|key| key.is_exact_instance_of::<PyString>())
    {
        return Ok(None);
    }
    let result = PyDict::new(py);
    for name in names.iter() {
        let name = name.cast_into::<PyString>()?;
        result.set_item(&name, source.getattr(&name)?)?;
    }
    Ok(Some(result))
}

#[pyfunction]
pub(crate) fn format_settings_emoji_choices<'py>(
    py: Python<'py>,
    choices: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    let Ok(choices) = choices.cast_exact::<PyTuple>() else {
        return Ok(None);
    };
    let result = PyList::empty(py);
    for choice in choices.iter() {
        let Ok(choice) = choice.cast_exact::<PyTuple>() else {
            return Ok(None);
        };
        if choice.len() != 2 {
            return Ok(None);
        }
        let (key, text) = (choice.get_item(0)?, choice.get_item(1)?);
        if !key.is_exact_instance_of::<PyString>() || !text.is_exact_instance_of::<PyString>() {
            return Ok(None);
        }
        let formatted = PyDict::new(py);
        formatted.set_item("key", key)?;
        formatted.set_item("text", text)?;
        result.append(formatted)?;
    }
    Ok(Some(result))
}

fn sound_stem(filename: &str) -> Option<&str> {
    let stem = filename.strip_suffix(".ogg")?;
    (!stem.is_empty() && !stem.contains('.')).then_some(stem)
}

#[pyfunction]
pub(crate) fn format_settings_notification_sounds(
    filenames: &Bound<'_, PyAny>,
) -> Option<Vec<String>> {
    let filenames = filenames.cast_exact::<PyList>().ok()?;
    let mut sounds = Vec::new();
    for filename in filenames.iter() {
        let filename = filename.cast_exact::<PyString>().ok()?.to_str().ok()?;
        if filename.contains(['/', '\\']) {
            return None;
        }
        if let Some(stem) = sound_stem(filename) {
            sounds.push(stem.to_owned());
        }
    }
    sounds.sort_unstable();
    Some(sounds)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selects_plain_ogg_files_without_hash_or_dot_prefix() {
        assert_eq!(sound_stem("sample.ogg"), Some("sample"));
        assert_eq!(sound_stem("آهنگ.ogg"), Some("آهنگ"));
        for other in [
            ".ogg",
            ".sample.ogg",
            "sample.123.ogg",
            "sample.OGG",
            "sample.mp3",
        ] {
            assert_eq!(sound_stem(other), None);
        }
    }
}

#[pyfunction]
pub(crate) fn append_prepared_server_settings<'py>(
    result: &Bound<'py, PyAny>,
    context: &Bound<'py, PyAny>,
    fields: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let (Ok(result), Ok(context), Ok(fields)) = (
        result.cast_exact::<PyDict>(),
        context.cast_exact::<PyDict>(),
        fields.cast_exact::<PyTuple>(),
    ) else {
        return Ok(None);
    };
    if ![result, context].into_iter().all(|values| {
        values
            .iter()
            .all(|(key, _)| key.is_exact_instance_of::<PyString>())
    }) {
        return Ok(None);
    }
    let prepared = result.copy()?;
    for field in fields.iter() {
        if !field.is_exact_instance_of::<PyString>() {
            return Ok(None);
        }
        let Some(value) = context.get_item(&field)? else {
            return Ok(None);
        };
        if !value.is_none() {
            prepared.set_item(&field, value)?;
        }
    }
    let Some(realm_url) = prepared.get_item("realm_url")? else {
        return Ok(None);
    };
    prepared.set_item("realm_uri", realm_url)?;
    Ok(Some(prepared))
}
