//! Plan inline formatting in an indexed graph before committing changes to a host tree.
//!
//! This module has no Python dependency. The caller validates its handler registry,
//! represents shared nodes with shared indices, and supplies a guard for handlers
//! that still require the host renderer. A rejected guard discards the whole plan.

use std::collections::VecDeque;

use crate::draft_text::is_python_whitespace;
use crate::markdown_code::find_inline_code;
use crate::markdown_emphasis::find_emphasis;
use crate::markdown_inline_stash::Scanner;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Text {
    pub value: String,
    pub atomic: bool,
}

impl Text {
    fn plain(value: impl Into<String>) -> Self {
        Self {
            value: value.into(),
            atomic: false,
        }
    }
}

#[derive(Debug)]
pub struct Node {
    pub tag: String,
    /// The host renderer's lowercase form of the tag.
    pub lower_tag: String,
    pub text: Option<Text>,
    pub tail: Option<Text>,
    pub children: Vec<usize>,
}

impl Node {
    fn new(tag: &str) -> Self {
        Self {
            tag: tag.to_owned(),
            lower_tag: tag.to_owned(),
            text: None,
            tail: None,
            children: Vec::new(),
        }
    }
}

#[derive(Debug)]
pub enum Stashed {
    Node(usize),
    Text(Text),
}

pub struct Pattern {
    /// 0: guarded host handler; 1: code; 2: strong/em; 3: strong; 4: em;
    /// 5: strikethrough; 6: literal emphasis markers; 7: line break.
    pub kind: u8,
    pub excludes: Vec<String>,
}

pub struct State {
    pub nodes: Vec<Node>,
    pub parents: Vec<Option<usize>>,
    pub stash: Vec<Stashed>,
    pub parent_order: Vec<usize>,
    pub registry_used: bool,
    pub ancestors: Vec<String>,
}

struct Engine<'a, G> {
    state: State,
    patterns: &'a [Pattern],
    guard: G,
    remaining: usize,
}

impl<G: FnMut(usize, &str) -> Option<()>> Engine<'_, G> {
    fn step(&mut self, depth: usize) -> Option<()> {
        if depth > 64 || self.remaining == 0 || self.state.nodes.len() > 20_000 {
            return None;
        }
        self.remaining -= 1;
        Some(())
    }

    fn set_parent(&mut self, child: usize, parent: usize) -> Option<()> {
        let previous = self.state.parents.get_mut(child)?;
        if previous.is_none() {
            self.state.parent_order.push(child);
        }
        *previous = Some(parent);
        Some(())
    }

    fn add_node(&mut self, tag: &str) -> usize {
        let index = self.state.nodes.len();
        self.state.nodes.push(Node::new(tag));
        self.state.parents.push(None);
        index
    }

    fn handle(&mut self, data: Text, first: usize, depth: usize) -> Option<Text> {
        self.step(depth)?;
        if data.atomic {
            return Some(data);
        }
        self.state.registry_used |= !self.patterns.is_empty();
        let mut data = data.value;
        for index in first..self.patterns.len() {
            if self.patterns[index]
                .excludes
                .iter()
                .any(|tag| self.state.ancestors.contains(tag))
            {
                continue;
            }
            loop {
                self.step(depth)?;
                let kind = self.patterns[index].kind;
                let (before, after, mut stashed) = match kind {
                    0 => {
                        (self.guard)(index, &data)?;
                        break;
                    }
                    1 => {
                        let Some(matched) = find_inline_code(&data, 0) else {
                            break;
                        };
                        let begin = data.char_indices().nth(matched.start)?.0;
                        let end = data
                            .char_indices()
                            .nth(matched.end)
                            .map_or(data.len(), |(i, _)| i);
                        let value = matched.rendered_text();
                        let stashed = if matched.code.is_some() {
                            let node = self.add_node("code");
                            self.state.nodes[node].text = Some(Text {
                                value,
                                atomic: true,
                            });
                            Stashed::Node(node)
                        } else {
                            Stashed::Text(Text::plain(value))
                        };
                        (data[..begin].to_owned(), data[end..].to_owned(), stashed)
                    }
                    2..=5 => {
                        let tag = match kind {
                            2 => "strong,em",
                            3 => "strong",
                            4 => "em",
                            _ => "del",
                        };
                        let Some(matched) = find_emphasis(&data, tag) else {
                            break;
                        };
                        let node = self.add_node(if kind == 2 { "strong" } else { tag });
                        let text_node = if kind == 2 {
                            let child = self.add_node("em");
                            self.state.nodes[node].children.push(child);
                            child
                        } else {
                            node
                        };
                        self.state.nodes[text_node].text = Some(Text::plain(matched.text));
                        (
                            matched.before.to_owned(),
                            matched.after.to_owned(),
                            Stashed::Node(node),
                        )
                    }
                    6 => {
                        let Some((start, end)) = find_not_strong(&data) else {
                            break;
                        };
                        (
                            data[..start].to_owned(),
                            data[end..].to_owned(),
                            Stashed::Text(Text::plain(&data[start..end])),
                        )
                    }
                    7 => {
                        let Some(start) = data.find('\n') else {
                            break;
                        };
                        (
                            data[..start].to_owned(),
                            data[start + 1..].to_owned(),
                            Stashed::Node(self.add_node("br")),
                        )
                    }
                    _ => return None,
                };
                if let Stashed::Node(node) = &mut stashed {
                    let node = *node;
                    if !self.state.nodes[node]
                        .text
                        .as_ref()
                        .is_some_and(|text| text.atomic)
                    {
                        let mut children = vec![node];
                        children.extend_from_slice(&self.state.nodes[node].children);
                        for child in children {
                            if let Some(text) = self.state.nodes[child]
                                .text
                                .clone()
                                .filter(|t| !t.value.is_empty())
                            {
                                self.state
                                    .ancestors
                                    .push(self.state.nodes[child].lower_tag.clone());
                                let text = self.handle(text, index + 1, depth + 1)?;
                                self.state.nodes[child].text = Some(text);
                                self.state.ancestors.pop();
                            }
                            if let Some(tail) = self.state.nodes[child]
                                .tail
                                .clone()
                                .filter(|t| !t.value.is_empty())
                            {
                                let tail = self.handle(tail, index, depth + 1)?;
                                self.state.nodes[child].tail = Some(tail);
                            }
                        }
                    }
                }
                let id = self.state.stash.len();
                self.state.stash.push(stashed);
                data = format!("{before}\u{2}klzzwxh:{id:04}\u{3}{after}");
            }
        }
        Some(Text::plain(data))
    }

    fn link(&mut self, parent: usize, result: &[(usize, Vec<String>)], is_text: bool, text: Text) {
        if text.value.is_empty() {
            return;
        }
        let target = if let Some((last, _)) = result.last() {
            &mut self.state.nodes[*last].tail
        } else if is_text {
            &mut self.state.nodes[parent].text
        } else {
            &mut self.state.nodes[parent].tail
        };
        if let Some(previous) = target.as_mut().filter(|t| !t.value.is_empty()) {
            previous.value.push_str(&text.value);
            previous.atomic = false;
        } else {
            *target = Some(text);
        }
    }

    fn placeholders(
        &mut self,
        data: Option<Text>,
        parent: usize,
        is_text: bool,
        depth: usize,
    ) -> Option<Vec<(usize, Vec<String>)>> {
        self.step(depth)?;
        let mut result = Vec::new();
        let Some(data) = data else {
            return Some(result);
        };
        let atomic = data.atomic;
        let mut scanner = Scanner::new(data.value);
        let mut accepted = false;
        while let Some(step) = scanner.next(accepted) {
            self.step(depth)?;
            let Some(rejected) = step.rejected_text else {
                self.link(
                    parent,
                    &result,
                    is_text,
                    Text {
                        value: step.text,
                        atomic,
                    },
                );
                break;
            };
            let id = step.identifier.as_ref().and_then(|id| {
                let number = id.parse::<usize>().ok()?;
                (format!("{number:04}") == *id && number < self.state.stash.len()).then_some(number)
            });
            accepted = id.is_some();
            let Some(id) = id else {
                self.link(parent, &result, is_text, Text::plain(rejected));
                continue;
            };
            self.link(parent, &result, is_text, Text::plain(step.text));
            match &self.state.stash[id] {
                Stashed::Text(text) => {
                    self.link(parent, &result, is_text, text.clone());
                }
                Stashed::Node(node) => {
                    let node = *node;
                    let mut children = vec![node];
                    children.extend_from_slice(&self.state.nodes[node].children);
                    for child in children {
                        if self.state.nodes[child]
                            .tail
                            .as_ref()
                            .is_some_and(|t| t.value.chars().any(|c| !is_python_whitespace(c)))
                        {
                            self.element_text(node, child, false, depth + 1)?;
                        }
                        if self.state.nodes[child]
                            .text
                            .as_ref()
                            .is_some_and(|t| t.value.chars().any(|c| !is_python_whitespace(c)))
                        {
                            self.element_text(child, child, true, depth + 1)?;
                        }
                    }
                    result.push((node, self.state.ancestors.clone()));
                }
            }
        }
        Some(result)
    }

    fn element_text(
        &mut self,
        node: usize,
        subnode: usize,
        is_text: bool,
        depth: usize,
    ) -> Option<()> {
        self.step(depth)?;
        let data = if is_text {
            self.state.nodes[subnode].text.take()
        } else {
            self.state.nodes[subnode].tail.take()
        };
        let children = self.placeholders(data, subnode, is_text, depth + 1)?;
        let position = if !is_text && node != subnode {
            self.state.nodes[node]
                .children
                .iter()
                .position(|child| *child == subnode)?
                + 1
        } else {
            0
        };
        for (child, _) in children.into_iter().rev() {
            self.state.nodes[node].children.insert(position, child);
        }
        Some(())
    }

    fn run(mut self, ancestors: Vec<String>) -> Option<State> {
        let mut pending = vec![(0, 0)];
        while let Some((node, depth)) = pending.pop() {
            self.step(depth)?;
            let children = self.state.nodes.get(node)?.children.clone();
            for &child in &children {
                self.set_parent(child, node)?;
            }
            pending.extend(children.into_iter().rev().map(|child| (child, depth + 1)));
        }
        let mut stack = VecDeque::from([(0, ancestors)]);
        while let Some((current, parents)) = stack.pop_front() {
            self.step(0)?;
            self.state.ancestors = parents;
            let mut ancestors = Vec::new();
            let mut node = Some(current);
            while let Some(parent) = node {
                self.step(0)?;
                ancestors.push(self.state.nodes[parent].lower_tag.clone());
                node = self.state.parents[parent];
            }
            self.state.ancestors.extend(ancestors.into_iter().rev());
            let mut insert_queue = Vec::new();
            let mut position = 0;
            while position < self.state.nodes[current].children.len() {
                self.step(0)?;
                let child = self.state.nodes[current].children[position];
                if self.state.nodes[child]
                    .text
                    .as_ref()
                    .is_some_and(|t| !t.atomic && !t.value.is_empty())
                {
                    self.state
                        .ancestors
                        .push(self.state.nodes[child].lower_tag.clone());
                    let data = self.state.nodes[child].text.take()?;
                    let data = self.handle(data, 0, 0)?;
                    let children = self.placeholders(Some(data), child, true, 0)?;
                    for (new_child, _) in &children {
                        self.set_parent(*new_child, child)?;
                    }
                    stack.extend(children.iter().cloned());
                    insert_queue.push((child, children));
                    self.state.ancestors.pop();
                }
                if self.state.nodes[child]
                    .tail
                    .as_ref()
                    .is_some_and(|t| !t.value.is_empty())
                {
                    let tail = self.state.nodes[child].tail.take()?;
                    let tail = self.handle(tail, 0, 0)?;
                    let dummy = self.add_node("d");
                    let children = self.placeholders(Some(tail), dummy, false, 0)?;
                    self.state.nodes[child].tail = self.state.nodes[dummy].tail.take();
                    let at = self.state.nodes[current]
                        .children
                        .iter()
                        .position(|n| *n == child)?
                        + 1;
                    for (new_child, _) in children.into_iter().rev() {
                        self.set_parent(new_child, current)?;
                        self.state.nodes[current].children.insert(at, new_child);
                    }
                }
                if !self.state.nodes[child].children.is_empty() {
                    self.set_parent(child, current)?;
                    stack.push_back((child, self.state.ancestors.clone()));
                }
                position += 1;
            }
            for (node, children) in insert_queue {
                for (index, (child, _)) in children.into_iter().enumerate() {
                    self.state.nodes[node].children.insert(index, child);
                }
            }
        }
        Some(self.state)
    }
}

fn find_not_strong(data: &str) -> Option<(usize, usize)> {
    let mut previous = None;
    for (start, c) in data.char_indices() {
        if matches!(c, '*' | '_') && previous.is_none_or(is_python_whitespace) {
            let length = data[start..].chars().take_while(|next| *next == c).count();
            let end = start + length;
            if length <= 3 && data[end..].chars().next().is_none_or(is_python_whitespace) {
                return Some((start, end));
            }
        }
        previous = Some(c);
    }
    None
}

/// Return a complete plan rooted at node 0, or delegate the entire run.
///
/// The guard receives a pattern index and the text remaining at that priority.
/// It must return `Some(())` only when the host handler would have no effect.
/// It must not mutate host state, since a later guard or resource limit can
/// reject the plan. Invalid indices, cycles, and depth or work limits also
/// return `None`. Returned nodes include detached stash and temporary nodes;
/// the caller commits graph edges, parent mappings and stash identities together.
pub fn run(
    nodes: Vec<Node>,
    patterns: &[Pattern],
    ancestors: Vec<String>,
    guard: impl FnMut(usize, &str) -> Option<()>,
) -> Option<State> {
    if nodes.is_empty() || nodes.len() > 20_000 {
        return None;
    }
    let parents = vec![None; nodes.len()];
    Engine {
        state: State {
            nodes,
            parents,
            stash: Vec::new(),
            parent_order: Vec::new(),
            registry_used: false,
            ancestors: Vec::new(),
        },
        patterns,
        guard,
        remaining: 100_000,
    }
    .run(ancestors)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_invalid_graphs_and_unknown_handlers() {
        assert!(run(vec![], &[], vec![], |_, _| Some(())).is_none());
        let mut root = Node::new("div");
        root.children = vec![1];
        assert!(run(vec![root], &[], vec![], |_, _| Some(())).is_none());
        let mut root = Node::new("div");
        root.children = vec![0];
        assert!(run(vec![root], &[], vec![], |_, _| Some(())).is_none());
        let mut root = Node::new("div");
        root.children = vec![1];
        let mut paragraph = Node::new("p");
        paragraph.text = Some(Text::plain("text"));
        assert!(
            run(
                vec![root, paragraph],
                &[Pattern {
                    kind: 255,
                    excludes: vec![]
                }],
                vec![],
                |_, _| Some(())
            )
            .is_none()
        );
    }

    #[test]
    fn rejected_guard_discards_the_entire_plan() {
        let mut root = Node::new("div");
        root.children = vec![1];
        let mut paragraph = Node::new("p");
        paragraph.text = Some(Text::plain("**bold** unsupported"));
        let patterns = [
            Pattern {
                kind: 3,
                excludes: vec![],
            },
            Pattern {
                kind: 0,
                excludes: vec![],
            },
        ];
        let mut calls = 0;
        assert!(
            run(vec![root, paragraph], &patterns, vec![], |_, _| {
                calls += 1;
                None
            })
            .is_none()
        );
        assert_eq!(calls, 1);
    }

    #[test]
    fn atomic_text_does_not_use_the_registry() {
        let mut root = Node::new("div");
        root.children = vec![1];
        let mut paragraph = Node::new("p");
        paragraph.text = Some(Text {
            value: "**atomic**".to_owned(),
            atomic: true,
        });
        let result = run(
            vec![root, paragraph],
            &[Pattern {
                kind: 0,
                excludes: vec![],
            }],
            vec![],
            |_, _| panic!("atomic string matched"),
        )
        .unwrap();
        assert!(!result.registry_used);
        assert!(result.stash.is_empty());
        assert_eq!(result.nodes[1].text.as_ref().unwrap().value, "**atomic**");
    }
}
