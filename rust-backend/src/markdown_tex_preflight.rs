//! Certify every normal TeX outcome before a caller can start rendering.
//!
//! This is preparation for a deferred-effect commit protocol. It does not call
//! renderers, validate the host registry, or restore host state on exceptions.
//! The Python adapter must continue delegating TeX until that protocol exists.

use crate::markdown_native_inline::{Inputs, Node, Pattern, Stashed, State, run_with_tex_outcomes};

const MAX_CALLS: usize = 6;
const MAX_BYTES: usize = 8 * 1024 * 1024;
const MAX_ITEMS: usize = 100_000;

pub struct TexPreflight {
    calls: Vec<String>,
    plans: Vec<State>,
}

#[derive(Debug, PartialEq, Eq)]
pub struct TexResultCountError {
    pub expected: usize,
    pub actual: usize,
}

impl TexPreflight {
    /// Renderer arguments in registry/graph encounter order, including repeats.
    pub fn calls(&self) -> &[String] {
        &self.calls
    }

    /// Select a previously certified plan without searching or invoking guards.
    ///
    /// A successful empty rendering is distinct from failure. Results are raw
    /// HTML stash values, never reparsed as inline Markdown. A count error is a
    /// caller protocol error; it must not trigger a retry through Python.
    /// This only materializes a final plan, not intermediate exception state.
    pub fn into_plan(self, results: Vec<Option<String>>) -> Result<State, TexResultCountError> {
        if results.len() != self.calls.len() {
            return Err(TexResultCountError {
                expected: self.calls.len(),
                actual: results.len(),
            });
        }
        let mask = results.iter().enumerate().fold(0, |mask, (index, value)| {
            mask | (usize::from(value.is_some()) << index)
        });
        let mut plans = self.plans;
        let mut plan = plans.swap_remove(mask);
        for (call, result) in plan.tex.iter().zip(results) {
            if let Some(block) = call.html_block {
                plan.html_blocks[block] = result.unwrap();
            }
        }
        Ok(plan)
    }
}

fn charge_nodes(nodes: &[Node], bytes: &mut usize) -> Option<()> {
    for node in nodes {
        for value in [&node.tag, &node.lower_tag]
            .into_iter()
            .chain(node.text.iter().map(|text| &text.value))
            .chain(node.tail.iter().map(|text| &text.value))
            .chain(node.attributes.iter().flat_map(|(key, value)| [key, value]))
        {
            *bytes = bytes.checked_sub(value.len())?;
        }
    }
    Some(())
}

fn charge_plan(plan: &State, bytes: &mut usize, items: &mut usize) -> Option<()> {
    for length in [
        plan.nodes.len(),
        plan.parents.len(),
        plan.stash.len(),
        plan.parent_order.len(),
        plan.ancestors.len(),
        plan.attachment_paths.len(),
        plan.html_blocks.len(),
        plan.tex.len(),
        plan.mentions.users.len(),
        plan.mentions.groups.len(),
    ] {
        *items = items.checked_sub(length)?;
    }
    charge_nodes(&plan.nodes, bytes)?;
    for value in plan
        .nodes
        .iter()
        .map(|node| node.children.len() + node.attributes.len())
    {
        *items = items.checked_sub(value)?;
    }
    for value in plan
        .stash
        .iter()
        .filter_map(|stashed| match stashed {
            Stashed::Text(text) => Some(&text.value),
            Stashed::Node(_) => None,
        })
        .chain(&plan.html_blocks)
        .chain(&plan.ancestors)
        .chain(&plan.attachment_paths)
        .chain(plan.tex.iter().map(|call| &call.body))
    {
        *bytes = bytes.checked_sub(value.len())?;
    }
    Some(())
}

/// Check the whole graph for every combination of successful and failed TeX.
///
/// Pattern 22 is the guarded native TeX syntax. Supply the same immutable
/// registry, authorized snapshots and HTML counter as the eventual commit.
/// Guards must be pure: preflight can visit them repeatedly and discard every
/// plan. All branches must have the same ordered renderer arguments. A late
/// unsupported handler, resource limit, or result-dependent call sequence
/// rejects the entire preflight before any rendering.
///
/// At most six calls (64 combinations plus one discovery pass), 100,000 stored
/// graph items and 8 MiB of stored text are accepted. Each pass also has the
/// inline engine's existing work/depth limits. Larger graphs delegate.
pub fn preflight(
    nodes: Vec<Node>,
    patterns: &[Pattern],
    ancestors: Vec<String>,
    inputs: Inputs<'_>,
    mut guard: impl FnMut(usize, &str) -> Option<()>,
) -> Option<TexPreflight> {
    if nodes.is_empty() || nodes.len() > 20_000 || inputs.html_counter.is_none() {
        return None;
    }
    let mut input_bytes = MAX_BYTES;
    charge_nodes(&nodes, &mut input_bytes)?;
    for ancestor in &ancestors {
        input_bytes = input_bytes.checked_sub(ancestor.len())?;
    }
    let mut input_items = MAX_ITEMS;
    input_items = input_items.checked_sub(ancestors.len())?;
    for node in &nodes {
        input_items = input_items.checked_sub(node.children.len() + node.attributes.len())?;
    }
    let discovery = run_with_tex_outcomes(
        nodes.clone(),
        patterns,
        ancestors.clone(),
        inputs,
        Some(&[true; MAX_CALLS]),
        &mut guard,
    )?;
    let calls: Vec<_> = discovery.tex.iter().map(|call| call.body.clone()).collect();
    let mut bytes = MAX_BYTES;
    for call in &calls {
        bytes = bytes.checked_sub(call.len())?;
    }
    let mut items = MAX_ITEMS;
    let mut plans = Vec::new();
    let combinations = 1_usize << calls.len();
    let mut discovery = Some(discovery);
    for mask in 0..combinations {
        let outcomes = std::array::from_fn::<_, MAX_CALLS, _>(|index| mask & (1 << index) != 0);
        let plan = if mask + 1 == combinations {
            discovery.take()?
        } else {
            run_with_tex_outcomes(
                nodes.clone(),
                patterns,
                ancestors.clone(),
                inputs,
                Some(&outcomes),
                &mut guard,
            )?
        };
        if !plan.tex.iter().map(|call| &call.body).eq(&calls) {
            return None;
        }
        charge_plan(&plan, &mut bytes, &mut items)?;
        plans.push(plan);
    }
    Some(TexPreflight { calls, plans })
}
