//! Apply certified cumulative inline states without replacing existing elements.
//!
//! This internal building block does not validate a live renderer or registry.
//! It must not be connected to rendering without those guards and a policy for
//! callback mutations. A commit error never permits fallback or rerendering.

use std::collections::HashSet;

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::pyclass::{PyTraverseError, PyVisit};
use pyo3::types::{PyBool, PyDict, PyList, PySlice, PyString};

use crate::python::NativeInlineResult;

type ProtectedText = (Option<Py<PyAny>>, Option<Py<PyAny>>);

struct RegistryGuard {
    registry: Py<PyAny>,
    data: Py<PyDict>,
    entries: Vec<(Py<PyAny>, Py<PyAny>)>,
    priority: Py<PyList>,
    order: Vec<Py<PyAny>>,
    sorted: bool,
}

impl RegistryGuard {
    fn capture(processor: &Bound<'_, PyAny>) -> PyResult<Self> {
        let registry = processor.getattr("inlinePatterns")?;
        if !registry.get_type().is(&processor
            .py()
            .import("markdown.util")?
            .getattr("Registry")?)
        {
            return Err(invalid());
        }
        let data = registry.getattr("_data")?.cast_into_exact::<PyDict>()?;
        let priority = registry.getattr("_priority")?.cast_into_exact::<PyList>()?;
        let sorted = registry
            .getattr("_is_sorted")?
            .cast_into_exact::<PyBool>()?
            .is_true();
        let entries = data
            .iter()
            .map(|(key, value)| (key.unbind(), value.unbind()))
            .collect();
        let order = priority.iter().map(Bound::unbind).collect();
        Ok(Self {
            registry: registry.unbind(),
            data: data.unbind(),
            entries,
            priority: priority.unbind(),
            order,
            sorted,
        })
    }

    fn check(&self, processor: &Bound<'_, PyAny>) -> PyResult<()> {
        let py = processor.py();
        let registry = processor.getattr("inlinePatterns")?;
        if !registry.is(self.registry.bind(py)) {
            return Err(invalid());
        }
        let data = registry.getattr("_data")?;
        let priority = registry.getattr("_priority")?;
        let is_sorted = registry.getattr("_is_sorted")?;
        let data_original = self.data.bind(py);
        let priority_original = self.priority.bind(py);
        if !data.is(data_original)
            || !priority.is(priority_original)
            || !is_sorted.is_exact_instance_of::<PyBool>()
            || is_sorted.cast::<PyBool>()?.is_true() != self.sorted
            || data_original.len() != self.entries.len()
            || priority_original.len() != self.order.len()
            || data_original
                .iter()
                .zip(&self.entries)
                .any(|((key, value), expected)| {
                    !key.is(expected.0.bind(py)) || !value.is(expected.1.bind(py))
                })
            || priority_original
                .iter()
                .zip(&self.order)
                .any(|(item, expected)| !item.is(expected.bind(py)))
        {
            return Err(invalid());
        }
        Ok(())
    }

    fn traverse(&self, visit: &PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.registry)?;
        visit.call(&self.data)?;
        visit.call(&self.priority)?;
        for (key, value) in &self.entries {
            visit.call(key)?;
            visit.call(value)?;
        }
        for item in &self.order {
            visit.call(item)?;
        }
        Ok(())
    }
}

#[pyclass]
pub struct MarkdownInlineCheckpoint {
    processor: Option<Py<PyAny>>,
    elements: Vec<Py<PyAny>>,
    originals: usize,
    created: Vec<(String, Vec<(String, String)>)>,
    ordered: Option<Py<PyList>>,
    registry_guard: Option<RegistryGuard>,
    ancestor_lists: Vec<Py<PyList>>,
    ancestor_history: Option<Vec<Vec<String>>>,
    protected_text: Option<Vec<ProtectedText>>,
    html_counter: usize,
    html_length: usize,
    blocks: Vec<String>,
    paths: Vec<String>,
    users: HashSet<i32>,
    groups: HashSet<i32>,
    channel: bool,
    topic: bool,
    has_image: bool,
    applied: bool,
    failed: bool,
}

fn invalid() -> PyErr {
    PyValueError::new_err("inconsistent inline checkpoint")
}

#[pymethods]
impl MarkdownInlineCheckpoint {
    #[new]
    fn new(
        processor: Bound<'_, PyAny>,
        originals: Vec<Py<PyAny>>,
        ordered: Bound<'_, PyList>,
    ) -> PyResult<Self> {
        let stash = processor.getattr("md")?.getattr("htmlStash")?;
        Ok(Self {
            html_counter: stash.getattr("html_counter")?.extract()?,
            html_length: stash.getattr("rawHtmlBlocks")?.len()?,
            originals: originals.len(),
            elements: originals,
            processor: Some(processor.unbind()),
            ordered: Some(
                ordered
                    .call_method0("copy")?
                    .cast_into::<PyList>()?
                    .unbind(),
            ),
            registry_guard: None,
            created: Vec::new(),
            ancestor_lists: Vec::new(),
            ancestor_history: None,
            protected_text: None,
            blocks: Vec::new(),
            paths: Vec::new(),
            users: HashSet::new(),
            groups: HashSet::new(),
            channel: false,
            topic: false,
            has_image: false,
            applied: false,
            failed: false,
        })
    }

    fn protect_registry(&mut self, py: Python<'_>) -> PyResult<()> {
        if self.applied || self.failed || self.registry_guard.is_some() {
            return Err(invalid());
        }
        self.registry_guard = Some(RegistryGuard::capture(
            self.processor.as_ref().unwrap().bind(py),
        )?);
        Ok(())
    }

    #[pyo3(signature = (plan, paths, has_image, html_blocks, ancestor_history=None, untouched_text=None))]
    #[allow(clippy::too_many_arguments)]
    fn apply(
        &mut self,
        py: Python<'_>,
        plan: NativeInlineResult,
        paths: Vec<String>,
        has_image: bool,
        html_blocks: Vec<String>,
        ancestor_history: Option<Vec<Vec<String>>>,
        untouched_text: Option<Vec<(bool, bool)>>,
    ) -> PyResult<()> {
        if self.failed {
            return Err(PyRuntimeError::new_err(
                "inline checkpoint commit already failed",
            ));
        }
        let (nodes, parents, stash, ancestors, registry_used, mentions) = plan;
        let (users, groups, channel, topic) = mentions;
        let users: HashSet<_> = users.into_iter().collect();
        let groups: HashSet<_> = groups.into_iter().collect();
        let count = nodes.len();
        if count < self.elements.len()
            || !html_blocks.starts_with(&self.blocks)
            || !paths.starts_with(&self.paths)
            || !self.users.is_subset(&users)
            || !self.groups.is_subset(&groups)
            || (self.channel && !channel)
            || (self.topic && !topic)
            || (self.has_image && !has_image)
            || (self.applied && self.ancestor_history.is_some() != ancestor_history.is_some())
            || (self.applied && self.protected_text.is_some() != untouched_text.is_some())
            || untouched_text
                .as_ref()
                .is_some_and(|slots| slots.len() != self.originals)
            || self.ancestor_history.as_ref().is_some_and(|previous| {
                !ancestor_history
                    .as_ref()
                    .is_some_and(|history| history.starts_with(previous))
            })
            || nodes
                .iter()
                .any(|node| node.4.iter().any(|&child| child >= count))
            || parents
                .iter()
                .any(|&(child, parent)| child >= count || parent >= count)
            || stash.iter().any(|(node, text)| {
                node.is_some() == text.is_some() || node.is_some_and(|node| node >= count)
            })
            || self
                .created
                .iter()
                .zip(&nodes[self.originals..])
                .any(|((tag, attrs), node)| tag != &node.0 || attrs != &node.5)
        {
            return Err(invalid());
        }
        let processor = self.processor.as_ref().unwrap().bind(py);
        if let Some(guard) = &self.registry_guard {
            guard.check(processor)?;
        }
        let html = processor.getattr("md")?.getattr("htmlStash")?;
        let blocks = html.getattr("rawHtmlBlocks")?;
        let counter = self
            .html_counter
            .checked_add(self.blocks.len())
            .ok_or_else(invalid)?;
        let length = self
            .html_length
            .checked_add(self.blocks.len())
            .ok_or_else(invalid)?;
        let next_counter = self
            .html_counter
            .checked_add(html_blocks.len())
            .ok_or_else(invalid)?;
        if html.getattr("html_counter")?.extract::<usize>()? != counter || blocks.len()? != length {
            return Err(invalid());
        }
        let element = py.import("xml.etree.ElementTree")?.getattr("Element")?;
        let atomic = py.import("markdown.util")?.getattr("AtomicString")?;
        let protected_text: Option<Vec<ProtectedText>> = untouched_text
            .map(|slots| {
                slots
                    .into_iter()
                    .enumerate()
                    .map(|(index, (keep_text, keep_tail))| {
                        let target = self.elements[index].bind(py);
                        let prior = self.protected_text.as_ref().map(|values| &values[index]);
                        let keep =
                            |name,
                             preserve: bool,
                             value: &Option<(String, bool)>,
                             previous: Option<&Py<PyAny>>| {
                                if !preserve && previous.is_none() {
                                    return Ok(None);
                                }
                                let current = target.getattr(name)?;
                                if previous.is_some_and(|previous| !current.is(previous.bind(py))) {
                                    return Err(invalid());
                                }
                                if !preserve {
                                    return Ok(None);
                                }
                                if prior.is_some() && previous.is_none() {
                                    return Err(invalid());
                                }
                                let (text, is_atomic) = value.as_ref().ok_or_else(invalid)?;
                                let exact = if *is_atomic {
                                    current.get_type().is(&atomic)
                                } else {
                                    current.is_exact_instance_of::<PyString>()
                                };
                                if !exact
                                    || current.cast::<PyString>()?.to_str().ok()
                                        != Some(text.as_str())
                                {
                                    return Err(invalid());
                                }
                                Ok(Some(current.unbind()))
                            };
                        Ok((
                            keep(
                                "text",
                                keep_text,
                                &nodes[index].2,
                                prior.and_then(|pair| pair.0.as_ref()),
                            )?,
                            keep(
                                "tail",
                                keep_tail,
                                &nodes[index].3,
                                prior.and_then(|pair| pair.1.as_ref()),
                            )?,
                        ))
                    })
                    .collect::<PyResult<Vec<_>>>()
            })
            .transpose()?;
        let restore = |text: &Option<(String, bool)>| -> PyResult<Py<PyAny>> {
            match text {
                None => Ok(py.None()),
                Some((text, true)) => Ok(atomic.call1((text,))?.unbind()),
                Some((text, false)) => Ok(text.into_pyobject(py)?.into_any().unbind()),
            }
        };
        let mut elements: Vec<_> = self
            .elements
            .iter()
            .map(|value| value.clone_ref(py))
            .collect();
        for node in &nodes[elements.len()..] {
            let attrs = PyDict::new(py);
            for (key, value) in &node.5 {
                attrs.set_item(key, value)?;
            }
            elements.push(element.call1((&node.0, attrs))?.unbind());
        }
        let mut updates = Vec::new();
        for (index, node) in nodes.iter().enumerate() {
            let children = PyList::new(py, node.4.iter().map(|&child| elements[child].bind(py)))?;
            let keep = protected_text.as_ref().and_then(|values| values.get(index));
            let text = if keep.is_some_and(|pair| pair.0.is_some()) {
                None
            } else {
                Some(restore(&node.2)?)
            };
            let tail = if keep.is_some_and(|pair| pair.1.is_some()) {
                None
            } else {
                Some(restore(&node.3)?)
            };
            updates.push((text, tail, children));
        }
        let stashed = PyDict::new(py);
        for (index, (node, text)) in stash.iter().enumerate() {
            let value = if let Some(node) = node {
                elements[*node].clone_ref(py)
            } else {
                restore(text)?
            };
            stashed.set_item(format!("{index:04}"), value)?;
        }
        let parent_map = PyDict::new(py);
        for (child, parent) in parents {
            parent_map.set_item(elements[child].bind(py), elements[parent].bind(py))?;
        }
        let mut ancestor_lists: Vec<_> = self
            .ancestor_lists
            .iter()
            .map(|value| value.clone_ref(py))
            .collect();
        if let Some(history) = &ancestor_history {
            for values in history
                .iter()
                .chain(std::iter::once(&ancestors))
                .skip(ancestor_lists.len())
            {
                ancestor_lists.push(PyList::new(py, values)?.unbind());
            }
        }
        // An exception after host mutation leaves its partial state observable.
        // Poison the committer rather than permitting a retry of those effects.
        self.failed = true;
        for (target, (text, tail, children)) in elements.iter().zip(updates) {
            let target = target.bind(py);
            if let Some(text) = text {
                target.setattr("text", text)?;
            }
            if let Some(tail) = tail {
                target.setattr("tail", tail)?;
            }
            target.set_item(PySlice::full(py), children)?;
        }
        if registry_used {
            let registry = processor.getattr("inlinePatterns")?;
            registry
                .getattr("_priority")?
                .set_item(PySlice::full(py), self.ordered.as_ref().unwrap().bind(py))?;
            registry.setattr("_is_sorted", true)?;
        }
        if self.applied {
            for (name, value) in [("stashed_nodes", stashed), ("parent_map", parent_map)] {
                let target = processor.getattr(name)?;
                target.call_method0("clear")?;
                target.call_method1("update", (value,))?;
            }
        } else {
            processor.setattr("stashed_nodes", stashed)?;
            processor.setattr("parent_map", parent_map)?;
        }
        if let Some(history) = &ancestor_history {
            // Earlier lists may still be held by callbacks after traversal has
            // moved on. Finish those lists before selecting the current one.
            let first = self.ancestor_history.as_ref().map_or(0, Vec::len);
            for (target, values) in ancestor_lists[first..]
                .iter()
                .zip(history[first..].iter().chain(std::iter::once(&ancestors)))
            {
                target
                    .bind(py)
                    .as_any()
                    .set_item(PySlice::full(py), values)?;
            }
            processor.setattr("ancestors", ancestor_lists.last().unwrap().bind(py))?;
        } else {
            processor.setattr("ancestors", ancestors)?;
        }
        if !users.is_empty()
            || !groups.is_empty()
            || channel
            || topic
            || !paths.is_empty()
            || has_image
        {
            let rendering = processor.getattr("md")?.getattr("zulip_rendering_result")?;
            rendering.getattr("mentions_user_ids")?.call_method1(
                "update",
                (users.difference(&self.users).copied().collect::<Vec<_>>(),),
            )?;
            rendering.getattr("mentions_user_group_ids")?.call_method1(
                "update",
                (groups.difference(&self.groups).copied().collect::<Vec<_>>(),),
            )?;
            for (name, value) in [
                ("mentions_stream_wildcard", channel),
                ("mentions_topic_wildcard", topic),
                ("has_image", has_image),
            ] {
                if value {
                    rendering.setattr(name, true)?;
                }
            }
            rendering
                .getattr("potential_attachment_path_ids")?
                .call_method1("extend", (paths[self.paths.len()..].to_vec(),))?;
        }
        blocks.call_method1("extend", (html_blocks[self.blocks.len()..].to_vec(),))?;
        html.setattr("html_counter", next_counter)?;
        self.created = nodes[self.originals..]
            .iter()
            .map(|node| (node.0.clone(), node.5.clone()))
            .collect();
        self.elements = elements;
        self.blocks = html_blocks;
        self.paths = paths;
        self.users = users;
        self.groups = groups;
        self.channel = channel;
        self.topic = topic;
        self.has_image = has_image;
        self.applied = true;
        self.ancestor_lists = ancestor_lists;
        self.ancestor_history = ancestor_history;
        self.protected_text = protected_text;
        if registry_used && let Some(guard) = &mut self.registry_guard {
            guard.order = self
                .ordered
                .as_ref()
                .unwrap()
                .bind(py)
                .iter()
                .map(Bound::unbind)
                .collect();
            guard.sorted = true;
        }
        self.failed = false;
        Ok(())
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.processor)?;
        visit.call(&self.ordered)?;
        if let Some(guard) = &self.registry_guard {
            guard.traverse(&visit)?;
        }
        for element in &self.elements {
            visit.call(element)?;
        }
        for ancestors in &self.ancestor_lists {
            visit.call(ancestors)?;
        }
        if let Some(slots) = &self.protected_text {
            for (text, tail) in slots {
                visit.call(text)?;
                visit.call(tail)?;
            }
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        self.failed = true;
        self.processor = None;
        self.ordered = None;
        self.registry_guard = None;
        self.elements.clear();
        self.ancestor_lists.clear();
        self.protected_text = None;
    }
}
