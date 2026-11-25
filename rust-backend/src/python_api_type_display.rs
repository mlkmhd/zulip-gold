//! Literal label assembly for API documentation, over values prepared by Python.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyList, PyString};

#[pyfunction]
pub(crate) fn join_prepared_doc_type_labels<'py>(
    values: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let values = values.cast_exact::<PyList>().ok()?;
    if values.len() == 1 {
        let only = values.get_item(0).ok()?;
        return Some(only.cast_exact::<PyString>().ok()?.clone());
    }

    let mut output = String::new();
    for (index, value) in values.iter().enumerate() {
        let text = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
        if index != 0 {
            output.push_str(" | ");
        }
        output.push_str(text);
    }
    Some(PyString::new(values.py(), &output))
}

#[pyfunction]
pub(crate) fn wrap_prepared_doc_array_label(value: &Bound<'_, PyAny>) -> Option<String> {
    let text = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("({text})[]"))
}

#[pyfunction]
pub(crate) fn append_doc_nullable_label(value: &Bound<'_, PyAny>) -> Option<String> {
    let text = value.cast_exact::<PyString>().ok()?.to_str().ok()?;
    Some(format!("{text} | null"))
}

const MAX_TREE_DEPTH: usize = 32;
const MAX_TREE_NODES: usize = 1024;
const MAX_LABEL_BYTES: usize = 64 * 1024;

// Recognize only ordinary, bounded trees. Other mappings, iterables, objects,
// and limit failures are evaluated exclusively by the legacy Python renderer.
fn render_type<'py>(
    schema: &Bound<'py, PyAny>,
    depth: usize,
    remaining: &mut usize,
) -> Option<Bound<'py, PyString>> {
    if depth >= MAX_TREE_DEPTH {
        return None;
    }
    *remaining = remaining.checked_sub(1)?;
    let schema = schema.cast_exact::<PyDict>().ok()?;
    if schema.len() > 64
        || schema
            .iter()
            .any(|(key, _)| !key.is_exact_instance_of::<PyString>())
    {
        return None;
    }

    let py = schema.py();
    if let Some(union) = schema.get_item("oneOf").ok()? {
        let members = union.cast_exact::<PyList>().ok()?;
        if members.len() > *remaining {
            return None;
        }
        if members.len() == 1 {
            return render_type(&members.get_item(0).ok()?, depth + 1, remaining);
        }
        let mut result = String::new();
        for (index, member) in members.iter().enumerate() {
            let label = render_type(&member, depth + 1, remaining)?;
            let label = label.to_str().ok()?;
            if index != 0 {
                result.push_str(" | ");
            }
            if result.len().checked_add(label.len())? > MAX_LABEL_BYTES {
                return None;
            }
            result.push_str(label);
        }
        return Some(PyString::new(py, &result));
    }
    if let Some(items) = schema.get_item("items").ok()? {
        let label = render_type(&items, depth + 1, remaining)?;
        let label = label.to_str().ok()?;
        if label.len() > MAX_LABEL_BYTES - 4 {
            return None;
        }
        return Some(PyString::new(py, &format!("({label})[]")));
    }

    let label = schema.get_item("type").ok()??;
    let label = label.cast_exact::<PyString>().ok()?;
    if let Some(nullable) = schema.get_item("nullable").ok()?
        && nullable.cast_exact::<PyBool>().ok()?.is_true()
    {
        let label = label.to_str().ok()?;
        if label.len() > MAX_LABEL_BYTES - 7 {
            return None;
        }
        return Some(PyString::new(py, &format!("{label} | null")));
    }
    Some(label.clone())
}

#[pyfunction]
pub(crate) fn render_api_doc_type_tree<'py>(
    schema: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let mut budget = MAX_TREE_NODES;
    render_type(schema, 0, &mut budget)
}
