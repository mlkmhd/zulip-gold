//! Output-only projections from already prepared integration metadata.

use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString};

#[pyfunction]
pub(crate) fn round_integration_display_count(count: &Bound<'_, PyAny>) -> Option<i64> {
    let count = count.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    count.checked_sub(1)?.div_euclid(10).checked_mul(10)
}

type Categories<'py> = Vec<(Bound<'py, PyString>, Bound<'py, PyString>)>;

#[pyfunction]
pub(crate) fn project_integration_display_categories<'py>(
    available: &Bound<'py, PyAny>,
    names: &Bound<'py, PyAny>,
) -> Option<Categories<'py>> {
    let available = available.cast_exact::<PyDict>().ok()?;
    let names = names.cast_exact::<PyList>().ok()?;
    let mut by_name = HashMap::with_capacity(available.len());
    for (slug, name) in available.iter() {
        let slug = slug.cast_into_exact::<PyString>().ok()?;
        let name = name.cast_exact::<PyString>().ok()?.to_str().ok()?;
        // Reverse the catalog as Python does: last duplicate display wins.
        by_name.insert(name.to_owned(), slug);
    }
    names
        .iter()
        .map(|name| {
            let name = name.cast_into_exact::<PyString>().ok()?;
            let slug = by_name.get(name.to_str().ok()?)?.clone();
            Some((slug, name))
        })
        .collect()
}
