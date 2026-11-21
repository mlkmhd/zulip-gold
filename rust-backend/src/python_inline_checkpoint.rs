//! Apply certified cumulative inline states without replacing existing elements.
//!
//! This internal building block does not validate a live renderer or registry.
//! It must not be connected to rendering without those guards and a policy for
//! callback mutations. A commit error never permits fallback or rerendering.

use std::collections::HashSet;

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::pyclass::{PyTraverseError, PyVisit};
use pyo3::types::{PyDict, PyList, PySlice};

use crate::python::NativeInlineResult;

#[pyclass]
pub struct MarkdownInlineCheckpoint {
    processor: Option<Py<PyAny>>,
    elements: Vec<Py<PyAny>>,
    originals: usize,
    created: Vec<(String, Vec<(String, String)>)>,
    ordered: Option<Py<PyList>>,
    ancestor_lists: Vec<Py<PyList>>,
    ancestor_history: Option<Vec<Vec<String>>>,
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
            created: Vec::new(),
            ancestor_lists: Vec::new(),
            ancestor_history: None,
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

    #[pyo3(signature = (plan, paths, has_image, html_blocks, ancestor_history=None))]
    fn apply(
        &mut self,
        py: Python<'_>,
        plan: NativeInlineResult,
        paths: Vec<String>,
        has_image: bool,
        html_blocks: Vec<String>,
        ancestor_history: Option<Vec<Vec<String>>>,
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
        for node in &nodes {
            let children = PyList::new(py, node.4.iter().map(|&child| elements[child].bind(py)))?;
            updates.push((restore(&node.2)?, restore(&node.3)?, children));
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
            target.setattr("text", text)?;
            target.setattr("tail", tail)?;
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
        self.failed = false;
        Ok(())
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.processor)?;
        visit.call(&self.ordered)?;
        for element in &self.elements {
            visit.call(element)?;
        }
        for ancestors in &self.ancestor_lists {
            visit.call(ancestors)?;
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        self.failed = true;
        self.processor = None;
        self.ordered = None;
        self.elements.clear();
        self.ancestor_lists.clear();
    }
}
