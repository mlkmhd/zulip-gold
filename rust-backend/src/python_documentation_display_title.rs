//! Format already selected plain documentation title parts.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyString};

#[pyfunction]
pub(crate) fn format_documentation_display_title<'py>(
    py: Python<'py>,
    article_title: &Bound<'py, PyAny>,
    title_base: &Bound<'py, PyAny>,
    include_article_title: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let include = include_article_title.cast_exact::<PyBool>().ok()?.is_true();
    let base = title_base.cast_exact::<PyString>().ok()?;
    if !include {
        return Some(base.clone());
    }
    let article = article_title.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let base = base.to_str().ok()?;
    Some(PyString::new(py, &format!("{article} | {base}")))
}

#[pyfunction]
pub(crate) fn format_integration_documentation_title(label: &Bound<'_, PyAny>) -> Option<String> {
    let label = label.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("{label} | Zulip integrations"))
}
