#![cfg(feature = "native-markdown")]

use _zulip_backend::markdown_native_inline::{Node, Pattern, Stashed, Text, run};

fn node(tag: &str, text: Option<Text>, children: Vec<usize>) -> Node {
    Node {
        tag: tag.to_owned(),
        lower_tag: tag.to_lowercase(),
        text,
        tail: None,
        children,
    }
}

fn text(value: &str) -> Text {
    Text {
        value: value.to_owned(),
        atomic: false,
    }
}

fn paragraph(value: &str) -> Vec<Node> {
    vec![
        node("div", None, vec![1]),
        node("p", Some(text(value)), vec![]),
    ]
}

fn pattern(kind: u8) -> Pattern {
    Pattern {
        kind,
        excludes: vec![],
    }
}

#[test]
fn formats_unicode_code_and_emphasis_without_python() {
    let plan = run(
        paragraph("سلام **دنیا** و `a < b`"),
        &[pattern(1), pattern(3)],
        vec![],
        |_, _| panic!("all handlers are native"),
    )
    .unwrap();
    let paragraph = &plan.nodes[1];
    assert_eq!(paragraph.text.as_ref().unwrap(), &text("سلام "));
    let [strong, code] = paragraph.children.as_slice() else {
        panic!("expected strong and code children");
    };
    assert_eq!(plan.nodes[*strong].tag, "strong");
    assert_eq!(plan.nodes[*strong].text.as_ref().unwrap(), &text("دنیا"));
    assert_eq!(plan.nodes[*strong].tail.as_ref().unwrap(), &text(" و "));
    assert_eq!(plan.nodes[*code].tag, "code");
    assert_eq!(
        plan.nodes[*code].text,
        Some(Text {
            value: "a &lt; b".to_owned(),
            atomic: true,
        })
    );
    assert!(
        matches!(plan.stash.as_slice(), [Stashed::Node(a), Stashed::Node(b)] if a == code && b == strong)
    );
    assert_eq!(plan.parents[*strong], Some(1));
    assert_eq!(plan.parents[*code], Some(1));
}

#[test]
fn preserves_shared_node_indices_and_atomic_text() {
    let atomic = Text {
        value: "**literal** `code`".to_owned(),
        atomic: true,
    };
    let plan = run(
        vec![
            node("div", None, vec![1, 2]),
            node("p", None, vec![3]),
            node("section", None, vec![3]),
            node("span", Some(atomic.clone()), vec![]),
        ],
        &[pattern(1), pattern(3)],
        vec![],
        |_, _| panic!("atomic strings do not call guards"),
    )
    .unwrap();
    assert_eq!(plan.nodes[1].children, vec![3]);
    assert_eq!(plan.nodes[2].children, vec![3]);
    assert_eq!(plan.nodes[3].text, Some(atomic));
    assert_eq!(plan.parents[3], Some(2));
    assert_eq!(plan.parent_order, vec![1, 2, 3]);
    assert!(plan.stash.is_empty());
    assert!(!plan.registry_used);
}

#[test]
fn guards_see_remaining_text_after_native_handlers() {
    let mut calls = Vec::new();
    let plan = run(
        paragraph("`protected` **format** [host syntax]"),
        &[pattern(1), pattern(0), pattern(3)],
        vec![],
        |index, remaining| {
            calls.push((index, remaining.to_owned()));
            None
        },
    );
    assert!(plan.is_none());
    assert_eq!(
        calls,
        vec![(
            1,
            "\u{2}klzzwxh:0000\u{3} **format** [host syntax]".to_owned()
        )]
    );
}

#[test]
fn applies_ancestor_exclusions_before_calling_guards() {
    let plan = run(
        paragraph("**literal**"),
        &[Pattern {
            kind: 0,
            excludes: vec!["outer".to_owned()],
        }],
        vec!["outer".to_owned()],
        |_, _| panic!("excluded handler ran"),
    )
    .unwrap();
    assert_eq!(plan.nodes[1].text.as_ref().unwrap(), &text("**literal**"));
    assert!(plan.stash.is_empty());
}

#[test]
fn rejects_invalid_edges_cycles_and_excessive_depth() {
    assert!(run(vec![], &[], vec![], |_, _| Some(())).is_none());
    for child in [0, 1, usize::MAX] {
        assert!(
            run(vec![node("div", None, vec![child])], &[], vec![], |_, _| {
                Some(())
            })
            .is_none()
        );
    }
    let mut nodes: Vec<_> = (0..66)
        .map(|index| node("div", None, vec![index + 1]))
        .collect();
    nodes.push(node("p", Some(text("deep")), vec![]));
    assert!(run(nodes, &[], vec![], |_, _| Some(())).is_none());
}
