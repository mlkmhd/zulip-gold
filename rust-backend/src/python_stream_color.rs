use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PySet, PyString};

use crate::stream_color::{STREAM_ASSIGNMENT_COLORS, pick_colors};

#[pyfunction]
pub(crate) fn pick_exact_stock_channel_colors<'py>(
    used: &Bound<'py, PyAny>,
    explicit: &Bound<'py, PyAny>,
    ids: &Bound<'py, PyAny>,
    palette: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyDict>> {
    let used = used.cast_exact::<PySet>().ok()?;
    let explicit = explicit.cast_exact::<PyDict>().ok()?;
    let ids = ids.cast_exact::<PyList>().ok()?;
    let palette = palette.cast_exact::<PyList>().ok()?;
    if palette.len() != STREAM_ASSIGNMENT_COLORS.len() {
        return None;
    }

    let mut stock_values = HashMap::new();
    for (literal, color) in STREAM_ASSIGNMENT_COLORS.into_iter().zip(palette.iter()) {
        let color = color.cast_into_exact::<PyString>().ok()?;
        if color.to_str().ok()? != literal {
            return None;
        }
        stock_values.insert(literal, color);
    }

    let used_colors = used
        .iter()
        .map(|item| {
            Some(
                item.cast_exact::<PyString>()
                    .ok()?
                    .to_str()
                    .ok()?
                    .to_owned(),
            )
        })
        .collect::<Option<HashSet<_>>>()?;
    let mut explicit_colors = HashMap::new();
    let mut explicit_values = HashMap::new();
    for (key, value) in explicit.iter() {
        let id = key.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
        let color = value.cast_into_exact::<PyString>().ok()?;
        explicit_colors.insert(id, color.to_str().ok()?.to_owned());
        explicit_values.insert(id, color);
    }

    let mut sorted_input = Vec::with_capacity(ids.len());
    let mut keys = HashMap::new();
    for key in ids.iter() {
        let key = key.cast_into_exact::<PyInt>().ok()?;
        let id = key.extract::<i64>().ok()?;
        sorted_input.push(id);
        // Equal recipient IDs retain the first key object from Python's
        // stable sort, even when later duplicates change the assigned color.
        keys.entry(id).or_insert(key);
    }

    let result = PyDict::new(ids.py());
    for (id, color) in pick_colors(&used_colors, &explicit_colors, sorted_input) {
        let value = explicit_values
            .get(&id)
            .or_else(|| stock_values.get(color))?;
        result.set_item(keys.get(&id)?, value).ok()?;
    }
    Some(result)
}
