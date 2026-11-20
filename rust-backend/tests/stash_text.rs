#![cfg(feature = "native-markdown")]

use _zulip_backend::markdown_link_syntax::{parse, prepare_text, unescape};
use _zulip_backend::markdown_native_inline::{Node, Stashed, Text, snapshot_text_stash};

fn text(value: &str) -> Text {
    Text {
        value: value.to_owned(),
        atomic: false,
    }
}

fn node(value: &str, tail: &str, children: Vec<usize>) -> Node {
    Node {
        tag: "span".to_owned(),
        lower_tag: "span".to_owned(),
        text: Some(text(value)),
        tail: Some(text(tail)),
        children,
        attributes: vec![],
    }
}

#[test]
fn snapshots_nested_text_and_tails_in_document_order() {
    let nodes = vec![
        node("root", "EXCLUDED", vec![1, 3]),
        node("child", " child tail", vec![2]),
        node("🌍", " grandchild tail", vec![]),
        node("فارسی", " last tail", vec![]),
    ];
    let stash = snapshot_text_stash(&nodes, &[Stashed::Node(0), Stashed::Node(1)]).unwrap();
    assert_eq!(
        stash["0000"],
        "rootchild🌍 grandchild tail child tailفارسی last tail"
    );
    assert_eq!(stash["0001"], "child🌍 grandchild tail");
    assert_eq!(nodes[0].tail.as_ref().unwrap().value, "EXCLUDED");
    assert_eq!(nodes[0].children, [1, 3]);
}

#[test]
fn visits_shared_children_for_every_occurrence() {
    let nodes = vec![node("a", "excluded", vec![1, 1]), node("b", "c", vec![])];
    assert_eq!(
        snapshot_text_stash(&nodes, &[Stashed::Node(0)]).unwrap()["0000"],
        "abcbc"
    );
}

#[test]
fn preserves_atomic_and_nested_placeholder_text() {
    let placeholder = "\u{2}klzzwxh:0000\u{3}";
    let mut root = node(placeholder, "excluded", vec![]);
    root.text.as_mut().unwrap().atomic = true;
    let stash =
        snapshot_text_stash(&[root], &[Stashed::Text(text("target")), Stashed::Node(0)]).unwrap();
    assert_eq!(
        unescape("\u{2}klzzwxh:0001\u{3}", Some(&stash)),
        placeholder
    );
    assert_eq!(unescape(&stash["0001"], Some(&stash)), "target");
}

#[test]
fn prepares_link_text_from_an_arena_snapshot() {
    let nodes = vec![
        node("\"عنوان", "excluded", vec![1]),
        node(" 🌍", "\t\"", vec![]),
    ];
    let stash = snapshot_text_stash(
        &nodes,
        &[Stashed::Text(text(" example.com ")), Stashed::Node(0)],
    )
    .unwrap();
    let data = "[label](\u{2}klzzwxh:0000\u{3} '\u{2}klzzwxh:0001\u{3}')";
    let result = prepare_text(data, &parse(data, 0).unwrap().unwrap(), Some(&stash)).unwrap();
    assert_eq!(result.href, "example.com");
    assert_eq!(result.title.as_deref(), Some("عنوان 🌍 "));
}

#[test]
fn handles_empty_values_and_identifiers_above_four_digits() {
    let mut root = node("", "excluded", vec![]);
    root.text = None;
    let mut stash = (0..10_001)
        .map(|_| Stashed::Text(text("")))
        .collect::<Vec<_>>();
    stash[10_000] = Stashed::Node(0);
    let result = snapshot_text_stash(&[root], &stash).unwrap();
    assert_eq!(result.len(), 10_001);
    assert_eq!(result["10000"], "");
    assert!(!result.contains_key("0"));
    assert!(snapshot_text_stash(&[], &[]).unwrap().is_empty());
}

#[test]
fn rejects_cycles_and_invalid_indices() {
    assert!(snapshot_text_stash(&[], &[Stashed::Node(0)]).is_none());
    for children in [vec![0], vec![1], vec![usize::MAX]] {
        assert!(snapshot_text_stash(&[node("", "", children)], &[Stashed::Node(0)]).is_none());
    }
    let nodes = vec![node("", "", vec![1]), node("", "", vec![0])];
    assert!(snapshot_text_stash(&nodes, &[Stashed::Node(0)]).is_none());
}

#[test]
fn traverses_deep_trees_without_recursion() {
    let mut nodes = (0..10_000)
        .map(|i| node("x", "", vec![i + 1]))
        .collect::<Vec<_>>();
    nodes.last_mut().unwrap().children.clear();
    assert_eq!(
        snapshot_text_stash(&nodes, &[Stashed::Node(0)]).unwrap()["0000"],
        "x".repeat(10_000)
    );
}

#[test]
fn rejects_excessive_text_and_shared_graph_expansion() {
    let text = Text {
        value: "x".repeat(8 * 1024 * 1024 + 1),
        atomic: false,
    };
    assert!(snapshot_text_stash(&[], &[Stashed::Text(text)]).is_none());
    let nodes = (0..20)
        .map(|i| node("", "", if i == 19 { vec![] } else { vec![i + 1, i + 1] }))
        .collect::<Vec<_>>();
    assert!(snapshot_text_stash(&nodes, &[Stashed::Node(0)]).is_none());
    assert!(snapshot_text_stash(&[node("", "", vec![0; 100_001])], &[Stashed::Node(0)]).is_none());
}
