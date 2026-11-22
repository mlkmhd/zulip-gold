use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::own_profile_payload::{CustomField, Facts, assemble};

// This is an in-process projection of already authorized Django data, not a
// storage lookup. None leaves the established Django formatter in charge.
#[pyfunction]
pub(crate) fn assemble_own_profile<'py>(
    py: Python<'py>,
    raw_facts: &Bound<'py, PyAny>,
    avatar_url: String,
    timezone: String,
    bot: (Option<i16>, Option<i32>, bool),
    raw_fields: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyBytes>>> {
    let (Ok(facts), Ok(custom_fields)) = (
        raw_facts.extract::<Facts>(),
        raw_fields.extract::<Vec<CustomField>>(),
    ) else {
        return Ok(None);
    };
    let result = assemble(
        facts,
        avatar_url,
        timezone,
        bot.0,
        bot.1,
        bot.2,
        custom_fields,
    );
    let json =
        serde_json::to_vec(&result).map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok(Some(PyBytes::new(py, &json)))
}
