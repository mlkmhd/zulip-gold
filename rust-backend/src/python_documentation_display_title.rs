//! Format already selected plain documentation title parts.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyString};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};

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
    let article = python_string_bytes(article_title.cast_exact::<PyString>().ok()?)?;
    let base = python_string_bytes(base)?;
    let mut title = Vec::with_capacity(article.len() + b" | ".len() + base.len());
    title.extend_from_slice(&article);
    title.extend_from_slice(b" | ");
    title.extend_from_slice(&base);
    python_string_from_bytes(py, &title)
}

#[pyfunction]
pub(crate) fn format_integration_documentation_title<'py>(
    label: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let part = python_string_bytes(label.cast_exact::<PyString>().ok()?)?;
    let suffix = b" | Zulip integrations";
    let mut title = Vec::with_capacity(part.len() + suffix.len());
    title.extend_from_slice(&part);
    title.extend_from_slice(suffix);
    python_string_from_bytes(label.py(), &title)
}
