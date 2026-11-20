#![cfg(feature = "native-markdown")]

use _zulip_backend::markdown_native_inline::{NamedEmoji, Node, Pattern, Stashed, Text, run};

fn node(tag: &str, text: Option<Text>, children: Vec<usize>) -> Node {
    Node {
        tag: tag.to_owned(),
        lower_tag: tag.to_lowercase(),
        text,
        tail: None,
        children,
        attributes: vec![],
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
        emoji: None,
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
            emoji: None,
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

fn emoji_pattern() -> Pattern {
    Pattern {
        kind: 8,
        excludes: vec![],
        emoji: Some(NamedEmoji {
            unicode: [("smile".to_owned(), "1f604".to_owned())].into(),
            realm: [("custom_name".to_owned(), "/uploads/<&>\".png".to_owned())].into(),
        }),
    }
}

#[test]
fn constructs_emoji_attributes_atomic_text_and_unknown_stash() {
    let mut nodes = paragraph("سلام :smile: :custom_name: :missing: :zulip:");
    nodes[1].attributes = vec![("title".to_owned(), "original".to_owned())];
    let plan = run(nodes, &[emoji_pattern()], vec![], |_, _| {
        panic!("named emoji is native")
    })
    .unwrap();
    assert_eq!(
        plan.nodes[1].attributes,
        [("title".into(), "original".into())]
    );
    let children = &plan.nodes[1].children;
    assert_eq!(children.len(), 3);
    let smile = &plan.nodes[children[0]];
    assert_eq!(smile.tag, "span");
    assert_eq!(
        smile.attributes,
        [
            ("class".into(), "emoji emoji-1f604".into()),
            ("title".into(), "smile".into()),
            ("role".into(), "img".into()),
            ("aria-label".into(), "smile".into()),
        ]
    );
    assert!(smile.text.as_ref().unwrap().atomic);
    let custom = &plan.nodes[children[1]];
    assert_eq!(custom.tag, "img");
    assert_eq!(
        custom.attributes[0],
        ("src".into(), "/uploads/<&>\".png".into())
    );
    assert_eq!(custom.attributes[3], ("title".into(), "custom name".into()));
    assert_eq!(custom.tail.as_ref().unwrap().value, " :missing: ");
    assert!(matches!(&plan.stash[2], Stashed::Text(t) if t.value == ":missing:"));
    assert_eq!(
        plan.nodes[children[2]].attributes[0].1,
        "/static/generated/emoji/images/emoji/unicode/zulip.png"
    );
}

#[test]
fn realm_catalog_overrides_unicode_and_zulip() {
    let mut emoji = emoji_pattern();
    let catalog = emoji.emoji.as_mut().unwrap();
    for name in ["smile", "zulip"] {
        catalog
            .realm
            .insert(name.to_owned(), format!("/custom/{name}.png"));
    }
    let plan = run(paragraph(":smile::zulip:"), &[emoji], vec![], |_, _| {
        Some(())
    })
    .unwrap();
    for (child, name) in plan.nodes[1].children.iter().zip(["smile", "zulip"]) {
        assert_eq!(plan.nodes[*child].tag, "img");
        assert_eq!(
            plan.nodes[*child].attributes[0].1,
            format!("/custom/{name}.png")
        );
    }
}

#[test]
fn protects_code_and_delegates_unicode_names_after_native_matches() {
    let plan = run(
        paragraph("`:smile:` **:smile:**"),
        &[pattern(1), pattern(3), emoji_pattern()],
        vec![],
        |_, _| Some(()),
    )
    .unwrap();
    let code = &plan.nodes[plan.nodes[1].children[0]];
    assert_eq!(code.tag, "code");
    assert_eq!(code.text.as_ref().unwrap().value, ":smile:");
    let strong = &plan.nodes[plan.nodes[1].children[1]];
    assert_eq!(plan.nodes[strong.children[0]].tag, "span");
    for value in [":smile: :نام:", ":smile: :a\u{301}:"] {
        assert!(
            run(
                paragraph(value),
                &[emoji_pattern()],
                vec![],
                |_, _| Some(())
            )
            .is_none()
        );
    }
    assert!(run(paragraph(":smile:"), &[pattern(8)], vec![], |_, _| Some(())).is_none());
}
