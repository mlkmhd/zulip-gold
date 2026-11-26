//! Literal label assembly for API documentation, over values prepared by Python.

use crate::python_text_bytes::{
    join_exact_python_strings, python_string_bytes, python_string_from_bytes,
};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyList, PyString, PyTuple};

#[pyfunction]
pub(crate) fn join_prepared_doc_type_labels<'py>(
    values: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    join_exact_python_strings(values, b" | ")
}

fn surround_exact_label<'py>(
    value: &Bound<'py, PyAny>,
    prefix: &[u8],
    suffix: &[u8],
) -> Option<Bound<'py, PyString>> {
    let value = value.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(value)?;
    let mut output = Vec::with_capacity(
        prefix
            .len()
            .checked_add(text.len())?
            .checked_add(suffix.len())?,
    );
    output.extend_from_slice(prefix);
    output.extend_from_slice(&text);
    output.extend_from_slice(suffix);
    python_string_from_bytes(value.py(), &output)
}

#[pyfunction]
pub(crate) fn wrap_prepared_doc_array_label<'py>(
    value: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    surround_exact_label(value, b"(", b")[]")
}

#[pyfunction]
pub(crate) fn append_doc_nullable_label<'py>(
    value: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    surround_exact_label(value, b"", b" | null")
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
        let mut result = Vec::new();
        for (index, member) in members.iter().enumerate() {
            let label = render_type(&member, depth + 1, remaining)?;
            let label = python_string_bytes(&label)?;
            if index != 0 {
                result.extend_from_slice(b" | ");
            }
            if result.len().checked_add(label.len())? > MAX_LABEL_BYTES {
                return None;
            }
            result.extend_from_slice(&label);
        }
        return python_string_from_bytes(py, &result);
    }
    if let Some(items) = schema.get_item("items").ok()? {
        let label = render_type(&items, depth + 1, remaining)?;
        if python_string_bytes(&label)?.len() > MAX_LABEL_BYTES - 4 {
            return None;
        }
        return surround_exact_label(label.as_any(), b"(", b")[]");
    }

    let label = schema.get_item("type").ok()??;
    let label = label.cast_exact::<PyString>().ok()?;
    if let Some(nullable) = schema.get_item("nullable").ok()?
        && nullable.cast_exact::<PyBool>().ok()?.is_true()
    {
        if python_string_bytes(label)?.len() > MAX_LABEL_BYTES - 7 {
            return None;
        }
        return surround_exact_label(label.as_any(), b"", b" | null");
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

#[pyfunction]
pub(crate) fn render_prepared_api_oneof<'py>(
    schema: &Bound<'py, PyAny>,
    name: &Bound<'py, PyAny>,
    renderer: &Bound<'py, PyAny>,
    markdown: &Bound<'py, PyAny>,
    item_template: &Bound<'py, PyAny>,
    outer_template: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = schema.py();
    let mut html = PyString::new(py, "").into_any();
    let concat = py.import("operator")?.getattr("iadd")?;
    for variant in schema.get_item("oneOf")?.try_iter()? {
        let variant = variant?;
        let (format, item) = if variant.contains("items")?
            && variant.get_item("items")?.contains("properties")?
        {
            let format = item_template.getattr("format")?;
            let item = renderer.getattr("render_object_details")?.call1((
                variant.get_item("items")?,
                name.str()?,
                true,
            ))?;
            (format, item)
        } else if variant.contains("properties")? {
            let format = item_template.getattr("format")?;
            let item =
                renderer
                    .getattr("render_object_details")?
                    .call1((&variant, name.str()?, true))?;
            (format, item)
        } else if variant.contains("description")? {
            let format = item_template.getattr("format")?;
            let item = markdown
                .getattr("convert")?
                .call1((variant.get_item("description")?,))?;
            (format, item)
        } else {
            continue;
        };
        let kwargs = PyDict::new(py);
        kwargs.set_item("item", item)?;
        html = concat.call1((html, format.call((), Some(&kwargs))?))?;
    }
    let kwargs = PyDict::new(py);
    kwargs.set_item("values", html)?;
    outer_template.call_method("format", (), Some(&kwargs))
}

#[pyfunction]
pub(crate) fn decorate_prepared_api_description<'py>(
    mut text: Bound<'py, PyAny>,
    parameter: &Bound<'py, PyAny>,
    json: &Bound<'py, PyAny>,
    code_template: &Bound<'py, PyAny>,
    join_enums: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = text.py();
    let add = py.import("operator")?.getattr("iadd")?;
    let enums = parameter
        .getattr("value_schema")?
        .call_method1("get", ("enum",))?;
    if !enums.is_none() {
        let values = PyList::empty(py);
        for entry in enums.try_iter()? {
            let entry = entry?;
            let format = code_template.getattr("format")?;
            let kwargs = PyDict::new(py);
            kwargs.set_item("value", json.call_method1("dumps", (entry,))?)?;
            values.append(format.call((), Some(&kwargs))?)?;
        }
        let line = PyString::new(py, "\nMust be one of: {}. ")
            .call_method1("format", (join_enums.call1((values,))?,))?;
        text = add.call1((text, line))?;
    }
    let default = parameter
        .getattr("value_schema")?
        .call_method1("get", ("default",))?;
    if !default.is_none() {
        let line = PyString::new(py, "\nDefaults to `{}`.")
            .call_method1("format", (json.call_method1("dumps", (default,))?,))?;
        text = add.call1((text, line))?;
    }
    Ok(text)
}

#[pyfunction]
pub(crate) fn prepare_api_doc_badges<'py>(
    parameter: &Bound<'py, PyAny>,
    description: &Bound<'py, PyAny>,
    validate: &Bound<'py, PyAny>,
) -> PyResult<(&'static str, &'static str)> {
    let path = parameter.getattr("kind")?.eq("path")?;
    if path && !parameter.getattr("required")?.is_truthy()? {
        return Err(pyo3::exceptions::PyAssertionError::new_err(()));
    }
    let required = match (parameter.getattr("required")?.is_truthy()?, path) {
        (true, true) => "<span class=\"api-argument-required\">required in path</span>",
        (true, false) => "<span class=\"api-argument-required\">required</span>",
        (false, _) => "<span class=\"api-argument-optional\">optional</span>",
    };
    validate.call1((parameter.getattr("deprecated")?, description))?;
    let deprecated = if parameter.getattr("deprecated")?.is_truthy()? {
        "<span class=\"api-argument-deprecated\">Deprecated</span>"
    } else {
        ""
    };
    Ok((required, deprecated))
}

#[pyfunction]
pub(crate) fn choose_prepared_api_property_detail<'py>(
    objects: &Bound<'py, PyAny>,
    value: &Bound<'py, PyAny>,
    data_type: &Bound<'py, PyAny>,
    renderer: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = objects.py();
    let empty = PyString::new(py, "");
    let rendered =
        if data_type.contains("object")? && objects.get_item(value)?.contains("properties")? {
            renderer
                .getattr("render_object_details")?
                .call1((objects.get_item(value)?, value.str()?))?
        } else if objects.get_item(value)?.contains("items")?
            && objects
                .get_item(value)?
                .get_item("items")?
                .contains("properties")?
        {
            renderer
                .getattr("render_object_details")?
                .call1((objects.get_item(value)?.get_item("items")?, value.str()?))?
        } else if objects.get_item(value)?.contains("oneOf")? {
            renderer
                .getattr("render_oneof_block")?
                .call1((objects.get_item(value)?, value.str()?))?
        } else {
            return Ok(empty.into_any());
        };
    empty.as_any().add(rendered)
}

#[pyfunction]
pub(crate) fn render_prepared_api_parameter_list<'py>(
    parameters: &Bound<'py, PyAny>,
    markdown: &Bound<'py, PyAny>,
    renderer: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyList>> {
    let py = parameters.py();
    let sorted = py.import("builtins")?.getattr("sorted")?;
    let get_key = py
        .import("operator")?
        .call_method1("attrgetter", ("deprecated",))?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("key", get_key)?;
    let ordered = sorted.call((parameters,), Some(&kwargs))?;
    let lines = PyList::empty(py);
    for parameter in ordered.try_iter()? {
        lines.append(renderer.call_method1("_render_parameter", (parameter?, markdown))?)?;
    }
    Ok(lines)
}

#[pyfunction]
pub(crate) fn render_prepared_api_object_properties<'py>(
    schema: &Bound<'py, PyAny>,
    name: &Bound<'py, PyAny>,
    oneof: &Bound<'py, PyAny>,
    renderer: &Bound<'py, PyAny>,
    markdown: &Bound<'py, PyAny>,
    templates: &Bound<'py, pyo3::types::PyTuple>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = schema.py();
    let objects = schema.call_method1("get", ("properties", PyDict::new(py)))?;
    let lines = PyList::empty(py);
    for value in objects.try_iter()? {
        let value = value?;
        if objects
            .get_item(&value)?
            .call_method1("get", ("readOnly", false))?
            .is_truthy()?
        {
            continue;
        }
        lines.append(renderer.call_method1(
            "_render_object_property",
            (schema, &objects, value, markdown),
        )?)?;
    }
    let oneof = oneof.is_truthy()?;
    let template = templates.get_item(usize::from(oneof))?.getattr("format")?;
    let fields = PyDict::new(py);
    if !oneof {
        fields.set_item("argument", name)?;
    }
    fields.set_item(
        "values",
        PyString::new(py, "\n").call_method1("join", (lines,))?,
    )?;
    template.call((), Some(&fields))
}

#[pyfunction]
pub(crate) fn expand_prepared_api_doc_macros<'py>(
    mut lines: Bound<'py, PyAny>,
    pattern: &Bound<'py, PyAny>,
    renderer: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = lines.py();
    let slice = py.get_type::<pyo3::types::PySlice>();
    let split_kwargs = PyDict::new(py);
    split_kwargs.set_item("maxsplit", 0)?;
    loop {
        let mut expanded = None;
        for line in lines.try_iter()? {
            let line = line?;
            let index: usize = lines.call_method1("index", (&line,))?.extract()?;
            let found = pattern.call_method1("search", (&line,))?;
            if !found.is_truthy()? {
                continue;
            }
            let doc = found.call_method1("group", (2,))?;
            let text = renderer.call_method1("_render_macro_document", (doc,))?;
            let split = pattern.call_method("split", (&line,), Some(&split_kwargs))?;
            let replacement = PyList::empty(py);
            replacement.append(split.get_item(0)?)?;
            let following = split.get_item(-1)?;
            replacement.call_method1("extend", (text,))?;
            replacement.append(following)?;
            let before = lines.get_item(slice.call1((py.None(), index))?)?;
            let after = lines.get_item(slice.call1((index + 1, py.None()))?)?;
            expanded = Some(before.add(replacement)?.add(after)?);
            break;
        }
        match expanded {
            Some(value) => lines = value,
            None => return Ok(lines),
        }
    }
}

/// Render a prepared documentation row with the existing Python formatters.
#[pyfunction]
pub(crate) fn render_prepared_api_parameter_row<'py>(
    parameter: &Bound<'py, PyAny>,
    markdown: &Bound<'py, PyAny>,
    renderer: &Bound<'py, PyAny>,
    template: &Bound<'py, PyAny>,
    formatters: &Bound<'py, PyTuple>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = parameter.py();
    let name = parameter.getattr("name")?;
    let description = formatters
        .get_item(0)?
        .call1((parameter.getattr("description")?, parameter))?;
    let data_type = formatters
        .get_item(1)?
        .call1((parameter.getattr("value_schema")?,))?;
    let example = formatters
        .get_item(2)?
        .call1((parameter.getattr("example")?,))?;
    let badges = formatters.get_item(3)?.call1((parameter, &description))?;
    let required = badges.get_item(0)?;
    let deprecated = badges.get_item(1)?;

    let mut details = PyString::new(py, "").into_any();
    if data_type.contains("object")? {
        let schema = parameter.getattr("value_schema")?;
        if schema.contains("items")? && schema.get_item("items")?.contains("properties")? {
            details = renderer
                .getattr("render_object_details")?
                .call1((schema.get_item("items")?, name.str()?))?;
        } else if schema.contains("properties")? {
            details = renderer
                .getattr("render_object_details")?
                .call1((schema, name.str()?))?;
        } else if schema.contains("oneOf")? {
            details = renderer
                .getattr("render_oneof_block")?
                .call1((schema, name.str()?))?;
        }
    }

    let format = template.getattr("format")?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("argument", name)?;
    kwargs.set_item("example", formatters.get_item(4)?.call1((example,))?)?;
    kwargs.set_item("required", required)?;
    kwargs.set_item("deprecated", deprecated)?;
    kwargs.set_item(
        "description",
        markdown.call_method1("convert", (description,))?,
    )?;
    kwargs.set_item("type", data_type)?;
    kwargs.set_item("object_details", details)?;
    format.call((), Some(&kwargs))
}

/// Render one prepared request property, retaining the original Markdown and encoding callbacks.
#[pyfunction]
pub(crate) fn render_prepared_api_property_row<'py>(
    schema: &Bound<'py, PyAny>,
    objects: &Bound<'py, PyAny>,
    value: &Bound<'py, PyAny>,
    markdown: &Bound<'py, PyAny>,
    renderer: &Bound<'py, PyAny>,
    templates: &Bound<'py, PyTuple>,
    formatters: &Bound<'py, PyTuple>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = schema.py();
    let description = if objects.get_item(value)?.contains("description")? {
        objects.get_item(value)?.get_item("description")?
    } else {
        PyString::new(py, "").into_any()
    };
    let additions = PyList::empty(py);
    let code = |entry: Bound<'py, PyAny>| -> PyResult<Bound<'py, PyAny>> {
        let kwargs = PyDict::new(py);
        kwargs.set_item("value", entry)?;
        templates
            .get_item(0)?
            .call_method("format", (), Some(&kwargs))
    };
    let default = objects
        .call_method1("get", (value, PyDict::new(py)))?
        .call_method1("get", ("default",))?;
    if !default.is_none() {
        let formatted = code(formatters.get_item(0)?.call1((default,))?)?;
        additions
            .append(PyString::new(py, "Defaults to {}. ").call_method1("format", (formatted,))?)?;
    }
    let enums = objects
        .call_method1("get", (value, PyDict::new(py)))?
        .call_method1("get", ("enum",))?;
    if !enums.is_none() {
        let formatted = PyList::empty(py);
        for entry in enums.try_iter()? {
            formatted.append(code(formatters.get_item(0)?.call1((entry?,))?)?)?;
        }
        let joined = formatters.get_item(2)?.call1((formatted,))?;
        additions
            .append(PyString::new(py, "Must be one of: {}. ").call_method1("format", (joined,))?)?;
    }
    if objects.get_item(value)?.contains("example")? {
        let example = formatters
            .get_item(0)?
            .call1((objects.get_item(value)?.get_item("example")?,))?;
        let formatted = code(formatters.get_item(1)?.call1((example,))?)?;
        let label = PyString::new(
            py,
            r#"<span class="api-argument-example-label">Example</span>: {}"#,
        );
        additions.append(label.call_method1("format", (formatted,))?)?;
    }

    let description = if additions.is_empty() {
        markdown.call_method1("convert", (description,))?
    } else {
        let details = PyString::new(py, "")
            .call_method1("join", (additions,))?
            .call_method0("strip")?;
        let format = templates.get_item(1)?.getattr("format")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item(
            "description",
            markdown.call_method1("convert", (description,))?,
        )?;
        kwargs.set_item("additional_information", details)?;
        format.call((), Some(&kwargs))?
    };

    let required = if schema.contains("required")? {
        if schema.get_item("required")?.contains(value)? {
            r#"<span class="api-argument-required">required</span>"#
        } else {
            r#"<span class="api-argument-optional">optional</span>"#
        }
    } else {
        ""
    };
    let data_type = formatters.get_item(3)?.call1((objects.get_item(value)?,))?;
    let detail = formatters
        .get_item(4)?
        .call1((renderer, objects, value, &data_type))?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("value", value)?;
    kwargs.set_item("data_type", data_type)?;
    kwargs.set_item("required", required)?;
    kwargs.set_item("description", description)?;
    kwargs.set_item("object_details", detail)?;
    templates
        .get_item(2)?
        .call_method("format", (), Some(&kwargs))
}
