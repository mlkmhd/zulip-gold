//! Experimental transport for certified TeX checkpoint states.
//!
//! A caller must still validate its live renderer and registry. This layer
//! neither mutates Python renderer state nor invokes renderer callbacks.

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

use crate::markdown_native_inline::{Inputs, Node, Pattern, State, Text};
use crate::markdown_tex_preflight::{TexPending, TexProgress, preflight};
use crate::python::{NativeInlinePlan, NativeInlinePlannedNode, serialize_inline_plan};

type ProvenanceCheckpoint = (NativeInlinePlan, Vec<Vec<String>>, Vec<(bool, bool)>);

#[pyclass]
pub(crate) struct MarkdownTexCheckpoint {
    state: Option<State>,
    pending: Option<TexPending>,
}

impl From<TexProgress> for MarkdownTexCheckpoint {
    fn from(TexProgress { state, next_call }: TexProgress) -> Self {
        Self {
            state: Some(state),
            pending: next_call,
        }
    }
}

#[pymethods]
impl MarkdownTexCheckpoint {
    #[getter]
    fn body(&self) -> Option<&str> {
        self.pending.as_ref().map(TexPending::body)
    }

    fn take_checkpoint(&mut self) -> PyResult<(NativeInlinePlan, Vec<Vec<String>>)> {
        let mut state = self
            .state
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("TeX checkpoint already taken"))?;
        let history = std::mem::take(&mut state.ancestor_history);
        Ok((serialize_inline_plan(state), history))
    }

    fn take_checkpoint_with_text(&mut self) -> PyResult<ProvenanceCheckpoint> {
        let mut state = self
            .state
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("TeX checkpoint already taken"))?;
        let history = std::mem::take(&mut state.ancestor_history);
        let untouched = std::mem::take(&mut state.untouched_text);
        Ok((serialize_inline_plan(state), history, untouched))
    }

    fn complete(&mut self, rendered: Option<String>) -> PyResult<Self> {
        if self.state.is_some() {
            return Err(PyRuntimeError::new_err("take the TeX checkpoint first"));
        }
        let pending = self
            .pending
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("no pending TeX renderer call"))?;
        Ok(pending.complete(rendered).into())
    }
}

#[pyfunction]
pub(crate) fn preflight_markdown_tex_checkpoint(
    nodes: Vec<NativeInlinePlannedNode>,
    patterns: Vec<(u8, Vec<String>)>,
    ancestors: Vec<String>,
    html_counter: usize,
) -> Option<MarkdownTexCheckpoint> {
    if patterns.iter().any(|(kind, _)| !matches!(kind, 20 | 22)) {
        return None;
    }
    let text = |(value, atomic)| Text { value, atomic };
    let nodes = nodes
        .into_iter()
        .map(
            |(tag, lower_tag, content, tail, children, attributes)| Node {
                tag,
                lower_tag,
                text: content.map(text),
                tail: tail.map(text),
                children,
                attributes,
            },
        )
        .collect();
    let patterns: Vec<_> = patterns
        .into_iter()
        .map(|(kind, excludes)| Pattern {
            kind,
            excludes,
            emoji: None,
            emoticons: None,
            unicode_emoji: None,
            links: None,
        })
        .collect();
    let checked = preflight(
        nodes,
        &patterns,
        ancestors,
        Inputs {
            html_counter: Some(html_counter),
            ..Inputs::default()
        },
        |_, _| None,
    )?;
    Some(checked.begin().into())
}
