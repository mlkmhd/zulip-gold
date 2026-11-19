use crate::draft_text::is_python_whitespace;

pub struct Node {
    pub tag: String,
    pub text: Option<String>,
    pub tail: Option<String>,
    pub block: bool,
    pub children: usize,
}

pub struct Change {
    pub node: usize,
    pub text: bool,
    pub atomic: bool,
    pub value: String,
}

fn blank(value: Option<&str>) -> bool {
    value.is_none_or(|value| value.chars().all(is_python_whitespace))
}

fn prettify_blocks(
    nodes: &mut [Node],
    children: &[Vec<usize>],
    index: usize,
    changes: &mut Vec<Change>,
) {
    if nodes[index].block && !matches!(nodes[index].tag.as_str(), "code" | "pre") {
        if blank(nodes[index].text.as_deref())
            && children[index]
                .first()
                .is_some_and(|&child| nodes[child].block)
        {
            changes.push(Change {
                node: index,
                text: true,
                atomic: false,
                value: "\n".to_owned(),
            });
        }
        for &child in &children[index] {
            if nodes[child].block {
                prettify_blocks(nodes, children, child, changes);
            }
        }
    }
    if blank(nodes[index].tail.as_deref()) {
        nodes[index].tail = Some("\n".to_owned());
        changes.push(Change {
            node: index,
            text: false,
            atomic: false,
            value: "\n".to_owned(),
        });
    }
}

pub fn prettify(mut nodes: Vec<Node>) -> Option<Vec<Change>> {
    if nodes.is_empty() {
        return None;
    }
    let mut children = vec![Vec::new(); nodes.len()];
    let mut pending = vec![(0, nodes[0].children)];
    for (index, node) in nodes.iter().enumerate().skip(1) {
        while pending.last().is_some_and(|&(_, remaining)| remaining == 0) {
            pending.pop();
        }
        if pending.len() > 100 {
            return None;
        }
        let (parent, remaining) = pending.last_mut()?;
        children[*parent].push(index);
        *remaining -= 1;
        pending.push((index, node.children));
    }
    if pending.iter().any(|&(_, remaining)| remaining != 0) {
        return None;
    }

    let mut changes = Vec::new();
    prettify_blocks(&mut nodes, &children, 0, &mut changes);
    for (index, node) in nodes.iter().enumerate() {
        if node.tag == "br" {
            changes.push(Change {
                node: index,
                text: false,
                atomic: false,
                value: if blank(node.tail.as_deref()) {
                    "\n".to_owned()
                } else {
                    format!("\n{}", node.tail.as_deref().unwrap())
                },
            });
        }
    }
    for (index, node) in nodes.iter().enumerate() {
        if node.tag == "pre"
            && let Some(&child) = children[index].first()
            && nodes[child].tag == "code"
            && children[child].is_empty()
            && let Some(text) = &nodes[child].text
        {
            changes.push(Change {
                node: child,
                text: true,
                atomic: true,
                value: format!("{}\n", text.trim_end_matches(is_python_whitespace)),
            });
        }
    }
    Some(changes)
}

#[cfg(test)]
mod tests {
    use super::{Node, prettify};

    fn node(tag: &str, text: Option<&str>, tail: Option<&str>, children: usize) -> Node {
        Node {
            tag: tag.to_owned(),
            text: text.map(str::to_owned),
            tail: tail.map(str::to_owned),
            block: true,
            children,
        }
    }

    #[test]
    fn processes_blocks_breaks_and_code_in_order() {
        let changes = prettify(vec![
            node("div", Some("\u{1c}"), None, 2),
            node("br", None, Some("فارسی"), 0),
            node("pre", None, Some("\u{a0}"), 1),
            node("code", Some("code\u{1f}\n"), None, 0),
        ])
        .unwrap();
        let changes: Vec<_> = changes
            .iter()
            .map(|change| {
                (
                    change.node,
                    change.text,
                    change.atomic,
                    change.value.as_str(),
                )
            })
            .collect();
        assert_eq!(
            changes,
            vec![
                (0, true, false, "\n"),
                (2, false, false, "\n"),
                (0, false, false, "\n"),
                (1, false, false, "\nفارسی"),
                (3, true, true, "code\n"),
            ]
        );
    }

    #[test]
    fn rejects_incomplete_multiple_root_and_deep_snapshots() {
        assert!(prettify(vec![]).is_none());
        assert!(prettify(vec![node("div", None, None, 1)]).is_none());
        assert!(prettify(vec![node("div", None, None, 0), node("p", None, None, 0)]).is_none());
        let mut nodes: Vec<_> = (0..101).map(|_| node("div", None, None, 1)).collect();
        nodes.push(node("p", None, None, 0));
        assert!(prettify(nodes).is_none());
    }
}
