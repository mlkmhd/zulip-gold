use pyo3::IntoPyObjectExt;
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PySlice, PyString};

fn unpack_inline_result<'py, const COUNT: usize>(
    result: Bound<'py, PyAny>,
) -> PyResult<[Bound<'py, PyAny>; COUNT]> {
    let result_type = result.get_type();
    if !result_type.hasattr("__iter__")? && !result_type.hasattr("__getitem__")? {
        return Err(PyTypeError::new_err(format!(
            "cannot unpack non-iterable {} object",
            result_type.name()?
        )));
    }
    let mut values = result.try_iter()?;
    let mut next_field = |index| {
        values.next().transpose()?.ok_or_else(|| {
            PyValueError::new_err(format!(
                "not enough values to unpack (expected {COUNT}, got {index})"
            ))
        })
    };
    let fields = (0..COUNT)
        .map(&mut next_field)
        .collect::<PyResult<Vec<_>>>()?;
    if values.next().transpose()?.is_some() {
        return Err(PyValueError::new_err(format!(
            "too many values to unpack (expected {COUNT})"
        )));
    }
    Ok(fields.try_into().unwrap())
}

#[pyfunction]
pub fn run_markdown_inline<'py>(
    processor: &Bound<'py, PyAny>,
    tree: Bound<'py, PyAny>,
    ancestors: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = processor.py();
    processor.setattr("stashed_nodes", PyDict::new(py))?;
    let tree_parents = if ancestors.is_none() {
        PyList::empty(py).into_any()
    } else {
        ancestors.get_item(PySlice::full(py))?
    };
    let parent_map = PyDict::new(py);
    for parent in tree.call_method0("iter")?.try_iter()? {
        let parent = parent?;
        for child in parent.try_iter()? {
            parent_map.set_item(child?, &parent)?;
        }
    }
    processor.setattr("parent_map", parent_map)?;
    let stack = PyList::empty(py);
    stack.append((&tree, tree_parents))?;

    while !stack.is_empty() {
        let [current, parents] = unpack_inline_result(stack.call_method1("pop", (0,))?)?;
        processor.setattr("ancestors", parents)?;
        let build_ancestors = processor.getattr("_InlineProcessor__build_ancestors")?;
        let ancestors = processor.getattr("ancestors")?;
        build_ancestors.call1((&current, ancestors))?;
        let mut insert_queue = Vec::new();
        for child in current.try_iter()? {
            let child = child?;
            let process_text = if child.getattr("text")?.is_truthy()? {
                let text = child.getattr("text")?;
                let atomic_string = py.import("markdown.util")?.getattr("AtomicString")?;
                !text.is_instance(&atomic_string)?
            } else {
                false
            };
            if process_text {
                let append = processor.getattr("ancestors")?.getattr("append")?;
                let tag = child.getattr("tag")?.call_method0("lower")?;
                append.call1((tag,))?;
                let text = child.getattr("text")?;
                child.setattr("text", py.None())?;
                let placeholders = processor.getattr("_InlineProcessor__processPlaceholders")?;
                let handle_inline = processor.getattr("_InlineProcessor__handleInline")?;
                let text = handle_inline.call1((text,))?;
                let children = placeholders.call1((text, &child))?;
                for item in children.try_iter()? {
                    let item = item?;
                    let parent_map = processor.getattr("parent_map")?;
                    parent_map.set_item(item.get_item(0)?, &child)?;
                }
                stack.call_method1("__iadd__", (&children,))?;
                insert_queue.push((child.clone(), children));
                processor.getattr("ancestors")?.call_method0("pop")?;
            }
            if child.getattr("tail")?.is_truthy()? {
                let handle_inline = processor.getattr("_InlineProcessor__handleInline")?;
                let tail = child.getattr("tail")?;
                let tail = handle_inline.call1((tail,))?;
                let dummy = py
                    .import("xml.etree.ElementTree")?
                    .call_method1("Element", ("d",))?;
                child.setattr("tail", py.None())?;
                let placeholders = processor.getattr("_InlineProcessor__processPlaceholders")?;
                let tail_result = placeholders.call1((tail, &dummy, false))?;
                if dummy.getattr("tail")?.is_truthy()? {
                    child.setattr("tail", dummy.getattr("tail")?)?;
                }
                let position = py
                    .import("builtins")?
                    .call_method1("list", (&current,))?
                    .call_method1("index", (&child,))?
                    .add(1)?;
                tail_result.call_method0("reverse")?;
                for new_child in tail_result.try_iter()? {
                    let new_child = new_child?;
                    let parent_map = processor.getattr("parent_map")?;
                    parent_map.set_item(new_child.get_item(0)?, &current)?;
                    let insert = current.getattr("insert")?;
                    insert.call1((&position, new_child.get_item(0)?))?;
                }
            }
            if child.len()? != 0 {
                processor
                    .getattr("parent_map")?
                    .set_item(&child, &current)?;
                let append = stack.getattr("append")?;
                let ancestors = processor
                    .getattr("ancestors")?
                    .get_item(PySlice::full(py))?;
                append.call1(((&child, ancestors),))?;
            }
        }
        for (element, children) in insert_queue {
            for (index, child) in children.try_iter()?.enumerate() {
                let new_child = child?.get_item(0)?;
                element.call_method1("insert", (index, new_child))?;
            }
        }
    }
    Ok(tree)
}

#[pyfunction]
pub fn apply_markdown_inline<'py>(
    processor: &Bound<'py, PyAny>,
    pattern: &Bound<'py, PyAny>,
    data: Bound<'py, PyAny>,
    pattern_index: Bound<'py, PyAny>,
    mut start_index: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = processor.py();
    for exclude in pattern.getattr("ANCESTOR_EXCLUDES")?.try_iter()? {
        let exclude = exclude?.call_method0("lower")?;
        if processor.getattr("ancestors")?.contains(exclude)? {
            return (data, false, 0).into_bound_py_any(py);
        }
    }

    let matches = pattern
        .call_method0("getCompiledRegExp")?
        .call_method1("finditer", (&data, &start_index))?;
    let mut accepted = None;
    for matched in matches.try_iter()? {
        let matched = matched?;
        let result = pattern.call_method1("handleMatch", (&matched, &data))?;
        let [node, start, end] = unpack_inline_result(result)?;
        if start.is_none() || end.is_none() {
            let end = matched.call_method1("end", (0,))?;
            start_index = py
                .import("operator")?
                .call_method1("iadd", (start_index, end))?;
            continue;
        }
        accepted = Some((matched, node, start, end));
        break;
    }
    let Some((matched, node, start, end)) = accepted else {
        return (data, false, 0).into_bound_py_any(py);
    };
    if !matched.is_truthy()? {
        return (data, false, 0).into_bound_py_any(py);
    }
    if node.is_none() {
        return (data, true, end).into_bound_py_any(py);
    }

    if !node.is_instance(&py.get_type::<PyString>())? {
        let text = node.getattr("text")?;
        let atomic_string = py.import("markdown.util")?.getattr("AtomicString")?;
        if !text.is_instance(&atomic_string)? {
            // Snapshot children before callbacks can mutate the element.
            let children = py
                .import("builtins")?
                .call_method1("list", (&node,))?
                .cast_into::<PyList>()?;
            children.insert(0, &node)?;
            for child in children.iter() {
                let is_string = py
                    .import("markdown.treeprocessors")?
                    .call_method1("isString", (&node,))?;
                if !is_string.is_truthy()? {
                    if child.getattr("text")?.is_truthy()? {
                        let append = processor.getattr("ancestors")?.getattr("append")?;
                        let tag = child.getattr("tag")?.call_method0("lower")?;
                        append.call1((tag,))?;
                        let handle = processor.getattr("_InlineProcessor__handleInline")?;
                        let text = child.getattr("text")?;
                        let index = pattern_index.add(1)?;
                        let text = handle.call1((text, index))?;
                        child.setattr("text", text)?;
                        processor.getattr("ancestors")?.call_method0("pop")?;
                    }
                    if child.getattr("tail")?.is_truthy()? {
                        let handle = processor.getattr("_InlineProcessor__handleInline")?;
                        let tail = child.getattr("tail")?;
                        let tail = handle.call1((tail, &pattern_index))?;
                        child.setattr("tail", tail)?;
                    }
                }
            }
        }
    }

    let stash = processor.getattr("_InlineProcessor__stashNode")?;
    let pattern_type = pattern.call_method0("type")?;
    let placeholder = stash.call1((node, pattern_type))?;
    let slice = py.import("builtins")?.getattr("slice")?;
    let prefix = data.get_item(slice.call1((py.None(), start))?)?;
    let suffix = data.get_item(slice.call1((end, py.None()))?)?;
    let result =
        PyString::new(py, "{}{}{}").call_method1("format", (prefix, placeholder, suffix))?;
    (result, true, 0).into_bound_py_any(py)
}

#[pyfunction]
pub fn handle_markdown_inline<'py>(
    processor: &Bound<'py, PyAny>,
    mut data: Bound<'py, PyAny>,
    mut pattern_index: usize,
) -> PyResult<Bound<'py, PyAny>> {
    let mut start_index = 0usize.into_bound_py_any(processor.py())?;
    let count = processor.getattr("inlinePatterns")?.len()?;
    while pattern_index < count {
        // Callbacks can replace the registry or its entries during rendering.
        let apply_pattern = processor.getattr("_InlineProcessor__applyPattern")?;
        let pattern = processor
            .getattr("inlinePatterns")?
            .get_item(pattern_index)?;
        let result = apply_pattern.call1((pattern, data, pattern_index, start_index))?;
        let [next_data, matched, next_start] = unpack_inline_result(result)?;
        data = next_data;
        start_index = next_start;
        if !matched.is_truthy()? {
            pattern_index += 1;
        }
    }
    Ok(data)
}
