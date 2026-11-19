use std::cell::Cell;

use pyo3::IntoPyObjectExt;
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PySlice, PyString, PyTuple};

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
    Runtime::default().run(processor, tree, ancestors)
}

#[pyfunction]
pub fn apply_markdown_inline<'py>(
    processor: &Bound<'py, PyAny>,
    pattern: &Bound<'py, PyAny>,
    data: Bound<'py, PyAny>,
    pattern_index: Bound<'py, PyAny>,
    start_index: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    Runtime::default().apply(processor, pattern, data, pattern_index, start_index)
}

#[pyfunction]
pub fn handle_markdown_inline<'py>(
    processor: &Bound<'py, PyAny>,
    data: Bound<'py, PyAny>,
    pattern_index: usize,
) -> PyResult<Bound<'py, PyAny>> {
    Runtime::default().handle(processor, data, pattern_index)
}

#[pyfunction]
pub fn run_markdown_inline_engine<'py>(
    processor: &Bound<'py, PyAny>,
    tree: Bound<'py, PyAny>,
    ancestors: Bound<'py, PyAny>,
    callbacks: Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let runtime = Runtime {
        callbacks: Some(callbacks),
        method_type: Some(processor.py().import("types")?.getattr("MethodType")?),
        ..Runtime::default()
    };
    runtime.run(processor, tree, ancestors)
}

#[derive(Default)]
struct Runtime<'py> {
    callbacks: Option<Bound<'py, PyDict>>,
    method_type: Option<Bound<'py, PyAny>>,
    depth: Cell<usize>,
}

struct InlineCallDepth<'a>(&'a Cell<usize>);

impl Drop for InlineCallDepth<'_> {
    fn drop(&mut self) {
        self.0.set(self.0.get() - 1);
    }
}

impl<'py> Runtime<'py> {
    fn call(
        &self,
        processor: &Bound<'py, PyAny>,
        callback: Bound<'py, PyAny>,
        name: &str,
        args: Bound<'py, PyTuple>,
    ) -> PyResult<Bound<'py, PyAny>> {
        if let (Some(callbacks), Some(method_type)) = (&self.callbacks, &self.method_type)
            && callback.get_type().is(method_type)
            && callback.getattr("__self__")?.is(processor)
            && callbacks.get_item(name)?.is_some_and(|original| {
                callback
                    .getattr("__func__")
                    .is_ok_and(|function| function.is(original))
            })
        {
            // Let Python enforce its recursion limit before native frames exhaust the stack.
            if self.depth.get() >= 64 {
                return callback.call1(args);
            }
            self.depth.set(self.depth.get() + 1);
            let _depth = InlineCallDepth(&self.depth);
            let py = processor.py();
            match name {
                "__handleInline" => {
                    let data = args.get_item(0)?;
                    if data.is_instance(&py.import("markdown.util")?.getattr("AtomicString")?)? {
                        return Ok(data);
                    }
                    let index = if args.len() == 1 {
                        0usize.into_bound_py_any(py)?
                    } else {
                        args.get_item(1)?
                    };
                    if index.is_exact_instance_of::<PyInt>()
                        && let Ok(index) = index.extract::<usize>()
                        && index <= isize::MAX as usize
                    {
                        return self.handle(processor, data, index);
                    }
                }
                "__applyPattern" => {
                    let pattern = args.get_item(0)?;
                    if pattern.is_instance(
                        &py.import("markdown.inlinepatterns")?
                            .getattr("InlineProcessor")?,
                    )? {
                        return self.apply(
                            processor,
                            &pattern,
                            args.get_item(1)?,
                            args.get_item(2)?,
                            args.get_item(3)?,
                        );
                    }
                }
                "__processPlaceholders" => {
                    return self.placeholders(
                        processor,
                        args.get_item(0)?,
                        args.get_item(1)?,
                        args.len() < 3 || args.get_item(2)?.is_truthy()?,
                    );
                }
                "__processElementText" => {
                    self.element_text(
                        processor,
                        args.get_item(0)?,
                        args.get_item(1)?,
                        args.len() < 3 || args.get_item(2)?.is_truthy()?,
                    )?;
                    return Ok(py.None().into_bound(py));
                }
                "__build_ancestors" => {
                    let mut parent = args.get_item(0)?;
                    let parents = args.get_item(1)?;
                    let ancestors = PyList::empty(py);
                    while !parent.is_none() {
                        ancestors.append(parent.getattr("tag")?.call_method0("lower")?)?;
                        parent = processor
                            .getattr("parent_map")?
                            .call_method1("get", (parent,))?;
                    }
                    ancestors.reverse()?;
                    parents.call_method1("extend", (ancestors,))?;
                    return Ok(py.None().into_bound(py));
                }
                "__stashNode" => {
                    let make = processor.getattr("_InlineProcessor__makePlaceholder")?;
                    let result = self.call(
                        processor,
                        make,
                        "__makePlaceholder",
                        (args.get_item(1)?,).into_pyobject(py)?,
                    )?;
                    let [placeholder, identifier] = unpack_inline_result(result)?;
                    processor
                        .getattr("stashed_nodes")?
                        .set_item(identifier, args.get_item(0)?)?;
                    return Ok(placeholder);
                }
                "__makePlaceholder" => {
                    let identifier = PyString::new(py, "%04d")
                        .rem(processor.getattr("stashed_nodes")?.len()?)?;
                    let placeholder = py
                        .import("markdown.util")?
                        .getattr("INLINE_PLACEHOLDER")?
                        .rem(&identifier)?;
                    return (placeholder, identifier).into_bound_py_any(py);
                }
                _ => unreachable!(),
            }
        }
        callback.call1(args)
    }

    fn link_text(
        &self,
        parent: &Bound<'py, PyAny>,
        result: &Bound<'py, PyList>,
        is_text: bool,
        text: Bound<'py, PyAny>,
    ) -> PyResult<()> {
        if !text.is_truthy()? {
            return Ok(());
        }
        let (target, attribute) = if !result.is_empty() {
            (result.get_item(result.len() - 1)?.get_item(0)?, "tail")
        } else {
            (parent.clone(), if is_text { "text" } else { "tail" })
        };
        let text = if target.getattr(attribute)?.is_truthy()? {
            target
                .py()
                .import("operator")?
                .call_method1("iadd", (target.getattr(attribute)?, text))?
        } else {
            text
        };
        target.setattr(attribute, text)
    }

    fn placeholders(
        &self,
        processor: &Bound<'py, PyAny>,
        data: Bound<'py, PyAny>,
        parent: Bound<'py, PyAny>,
        is_text: bool,
    ) -> PyResult<Bound<'py, PyAny>> {
        let py = processor.py();
        let result = PyList::empty(py);
        let mut start = 0usize.into_bound_py_any(py)?;
        let slice = py.import("builtins")?.getattr("slice")?;
        while data.is_truthy()? {
            let find = data.getattr("find")?;
            let prefix = processor.getattr("_InlineProcessor__placeholder_prefix")?;
            let index = find.call1((prefix, &start))?;
            if index.ne(-1)? {
                let find = processor.getattr("_InlineProcessor__findPlaceholder")?;
                let found = find.call1((&data, &index))?;
                let [identifier, end] = unpack_inline_result(found)?;
                if processor.getattr("stashed_nodes")?.contains(&identifier)? {
                    let node = processor
                        .getattr("stashed_nodes")?
                        .call_method1("get", (identifier,))?;
                    if index.gt(0)? {
                        let text = data.get_item(slice.call1((&start, &index))?)?;
                        self.link_text(&parent, &result, is_text, text)?;
                    }
                    if !node.is_instance(&py.get_type::<PyString>())? {
                        let children = py
                            .import("builtins")?
                            .call_method1("list", (&node,))?
                            .cast_into::<PyList>()?;
                        children.insert(0, &node)?;
                        for child in children.iter() {
                            if child.getattr("tail")?.is_truthy()?
                                && child.getattr("tail")?.call_method0("strip")?.is_truthy()?
                            {
                                let process =
                                    processor.getattr("_InlineProcessor__processElementText")?;
                                self.call(
                                    processor,
                                    process,
                                    "__processElementText",
                                    (&node, &child, false).into_pyobject(py)?,
                                )?;
                            }
                            if child.getattr("text")?.is_truthy()?
                                && child.getattr("text")?.call_method0("strip")?.is_truthy()?
                            {
                                let process =
                                    processor.getattr("_InlineProcessor__processElementText")?;
                                self.call(
                                    processor,
                                    process,
                                    "__processElementText",
                                    (&child, &child).into_pyobject(py)?,
                                )?;
                            }
                        }
                    } else {
                        self.link_text(&parent, &result, is_text, node)?;
                        start = end;
                        continue;
                    }
                    start = end;
                    result.append((
                        node,
                        processor
                            .getattr("ancestors")?
                            .get_item(PySlice::full(py))?,
                    ))?;
                } else {
                    let end = index.add(
                        processor
                            .getattr("_InlineProcessor__placeholder_prefix")?
                            .len()?,
                    )?;
                    self.link_text(
                        &parent,
                        &result,
                        is_text,
                        data.get_item(slice.call1((&start, &end))?)?,
                    )?;
                    start = end;
                }
            } else {
                let mut text = data.get_item(slice.call1((&start, py.None()))?)?;
                let atomic = py.import("markdown.util")?.getattr("AtomicString")?;
                if data.is_instance(&atomic)? {
                    text = atomic.call1((text,))?;
                }
                self.link_text(&parent, &result, is_text, text)?;
                break;
            }
        }
        Ok(result.into_any())
    }

    fn element_text(
        &self,
        processor: &Bound<'py, PyAny>,
        node: Bound<'py, PyAny>,
        subnode: Bound<'py, PyAny>,
        is_text: bool,
    ) -> PyResult<()> {
        let py = processor.py();
        let attribute = if is_text { "text" } else { "tail" };
        let text = subnode.getattr(attribute)?;
        subnode.setattr(attribute, py.None())?;
        let process = processor.getattr("_InlineProcessor__processPlaceholders")?;
        let children = self.call(
            processor,
            process,
            "__processPlaceholders",
            (text, &subnode, is_text).into_pyobject(py)?,
        )?;
        let position = if !is_text && !node.is(&subnode) {
            py.import("builtins")?
                .call_method1("list", (&node,))?
                .call_method1("index", (&subnode,))?
                .add(1)?
        } else {
            0usize.into_bound_py_any(py)?
        };
        children.call_method0("reverse")?;
        for child in children.try_iter()? {
            node.call_method1("insert", (&position, child?.get_item(0)?))?;
        }
        Ok(())
    }

    fn run(
        &self,
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
            self.call(
                processor,
                build_ancestors,
                "__build_ancestors",
                (&current, ancestors).into_pyobject(py)?,
            )?;
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
                    let placeholders =
                        processor.getattr("_InlineProcessor__processPlaceholders")?;
                    let handle_inline = processor.getattr("_InlineProcessor__handleInline")?;
                    let text = self.call(
                        processor,
                        handle_inline,
                        "__handleInline",
                        (text,).into_pyobject(py)?,
                    )?;
                    let children = self.call(
                        processor,
                        placeholders,
                        "__processPlaceholders",
                        (text, &child).into_pyobject(py)?,
                    )?;
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
                    let tail = self.call(
                        processor,
                        handle_inline,
                        "__handleInline",
                        (tail,).into_pyobject(py)?,
                    )?;
                    let dummy = py
                        .import("xml.etree.ElementTree")?
                        .call_method1("Element", ("d",))?;
                    child.setattr("tail", py.None())?;
                    let placeholders =
                        processor.getattr("_InlineProcessor__processPlaceholders")?;
                    let tail_result = self.call(
                        processor,
                        placeholders,
                        "__processPlaceholders",
                        (tail, &dummy, false).into_pyobject(py)?,
                    )?;
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

    fn apply(
        &self,
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
                            let text = self.call(
                                processor,
                                handle,
                                "__handleInline",
                                (text, index).into_pyobject(py)?,
                            )?;
                            child.setattr("text", text)?;
                            processor.getattr("ancestors")?.call_method0("pop")?;
                        }
                        if child.getattr("tail")?.is_truthy()? {
                            let handle = processor.getattr("_InlineProcessor__handleInline")?;
                            let tail = child.getattr("tail")?;
                            let tail = self.call(
                                processor,
                                handle,
                                "__handleInline",
                                (tail, &pattern_index).into_pyobject(py)?,
                            )?;
                            child.setattr("tail", tail)?;
                        }
                    }
                }
            }
        }

        let stash = processor.getattr("_InlineProcessor__stashNode")?;
        let pattern_type = pattern.call_method0("type")?;
        let placeholder = self.call(
            processor,
            stash,
            "__stashNode",
            (node, pattern_type).into_pyobject(py)?,
        )?;
        let slice = py.import("builtins")?.getattr("slice")?;
        let prefix = data.get_item(slice.call1((py.None(), start))?)?;
        let suffix = data.get_item(slice.call1((end, py.None()))?)?;
        let result =
            PyString::new(py, "{}{}{}").call_method1("format", (prefix, placeholder, suffix))?;
        (result, true, 0).into_bound_py_any(py)
    }

    fn handle(
        &self,
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
            let result = self.call(
                processor,
                apply_pattern,
                "__applyPattern",
                (pattern, data, pattern_index, start_index).into_pyobject(processor.py())?,
            )?;
            let [next_data, matched, next_start] = unpack_inline_result(result)?;
            data = next_data;
            start_index = next_start;
            if !matched.is_truthy()? {
                pattern_index += 1;
            }
        }
        Ok(data)
    }
}
