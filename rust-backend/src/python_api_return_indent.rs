//! LF indentation of already-selected documentation text.

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};
use pyo3::prelude::*;
use pyo3::types::PyString;

#[pyfunction]
pub(crate) fn indent_prepared_api_return_description<'py>(
    description: &Bound<'py, PyAny>,
    replacement: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let description = description.cast_exact::<PyString>().ok()?;
    let replacement = replacement.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let indent = replacement.strip_prefix('\n')?;
    if !indent.bytes().all(|byte| byte == b' ') {
        return None;
    }
    if indent.is_empty() {
        return Some(description.clone());
    }
    let text = python_string_bytes(description)?;
    if !text.contains(&b'\n') {
        return Some(description.clone());
    }

    let newline_count = text.iter().filter(|&&byte| byte == b'\n').count();
    let output_bytes = text
        .len()
        .checked_add(newline_count.checked_mul(indent.len())?)?;
    // Leave unusually large copies to the original Python implementation.
    if output_bytes > 8 * 1024 * 1024 {
        return None;
    }
    let mut output = Vec::with_capacity(output_bytes);
    for (index, segment) in text.split(|&byte| byte == b'\n').enumerate() {
        if index != 0 {
            output.extend_from_slice(replacement.as_bytes());
        }
        output.extend_from_slice(segment);
    }
    python_string_from_bytes(description.py(), &output)
}

/// Format a complete prepared response field without changing Python's unusual-input handling.
#[pyfunction]
#[pyo3(signature = (description, spacing, data_type, return_value))]
pub(crate) fn render_prepared_api_return_description(
    description: &Bound<'_, PyAny>,
    spacing: &Bound<'_, PyAny>,
    data_type: &Bound<'_, PyAny>,
    return_value: Option<&Bound<'_, PyAny>>,
) -> Option<String> {
    let description = description.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let spacing = spacing
        .cast_exact::<pyo3::types::PyInt>()
        .ok()?
        .extract::<i64>()
        .ok()?;
    let data_type = data_type.cast_exact::<PyString>().ok()?.to_str().ok()?;

    // Real schemas use small recursive indentation. Decline before allocation
    // when special values or Python's full integer behavior would be affected.
    if !(-4096..=4096).contains(&spacing) {
        return None;
    }
    let return_value = match return_value {
        Some(name) => Some(name.cast_exact::<PyString>().ok()?.to_str().ok()?),
        None => None,
    };
    let prefix_width = usize::try_from(spacing.max(0)).ok()?;
    let indent_width = usize::try_from((spacing + 4).max(0)).ok()?;
    let newlines = description.bytes().filter(|&byte| byte == b'\n').count();
    let formatted_budget = description
        .len()
        .checked_add(newlines.checked_mul(indent_width)?)?
        .checked_add(prefix_width + indent_width + 64)?
        .checked_add(return_value.map_or(0, str::len))?
        .checked_add(data_type.len())?;
    if formatted_budget > 8 * 1024 * 1024 {
        return None;
    }

    let prefix = " ".repeat(prefix_width);
    let indent = " ".repeat(indent_width);
    let description = description.replace('\n', &format!("\n{indent}"));

    if let Some(name) = return_value {
        return Some(format!(
            "{prefix}* `{name}`: <span class=\"api-field-type\">{data_type}</span>\n\n{indent}{description}"
        ));
    }
    if let Some((name, details)) = description.split_once(": ")
        && !name.contains('\n')
    {
        return Some(format!(
            "{prefix}* {name}: <span class=\"api-field-type\">{data_type}</span>\n\n{indent}{details}"
        ));
    }
    Some(format!("{prefix}* {description}"))
}
