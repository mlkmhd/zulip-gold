#![cfg(feature = "native-markdown")]

use _zulip_backend::markdown_links::Policy;
use _zulip_backend::markdown_native_inline::{
    Inputs, Node, Pattern, Stashed, Text, run_with_inputs,
};
use _zulip_backend::markdown_tex_preflight::{TexResultCountError, preflight};

fn node(tag: &str, text: Option<&str>, children: Vec<usize>) -> Node {
    Node {
        tag: tag.into(),
        lower_tag: tag.to_lowercase(),
        text: text.map(|value| Text {
            value: value.into(),
            atomic: false,
        }),
        tail: None,
        children,
        attributes: vec![],
    }
}

fn paragraph(text: &str) -> Vec<Node> {
    vec![node("div", None, vec![1]), node("p", Some(text), vec![])]
}

fn pattern(kind: u8) -> Pattern {
    Pattern {
        kind,
        excludes: vec![],
        emoji: None,
        emoticons: None,
        unicode_emoji: None,
        links: None,
    }
}

fn inputs() -> Inputs<'static> {
    Inputs {
        html_counter: Some(7),
        ..Inputs::default()
    }
}

fn link() -> Pattern {
    let mut link = pattern(11);
    link.links = Some(Policy {
        allowed_schemes: vec!["http".into(), "https".into()],
        netloc_schemes: vec!["http".into(), "https".into()],
        enable_file_links: false,
        realm_url: None,
    });
    link
}

#[test]
fn materializes_all_64_outcomes_without_reinvoking_guards() {
    for mask in 0..64 {
        let mut visits = 0;
        let preflight = preflight(
            paragraph("$$x$$ $$x$$ $$α$$ $$<script>$$ $$a\\$b$$ $$last$$"),
            &[pattern(22), pattern(0)],
            vec![],
            inputs(),
            |_, _| {
                visits += 1;
                Some(())
            },
        )
        .unwrap();
        assert_eq!(
            preflight.calls(),
            ["x", "x", "α", "<script>", "a\\$b", "last"]
        );
        let visited = visits;
        let results: Vec<_> = (0..6)
            .map(|index| {
                (mask & (1 << index) != 0).then(|| format!("<math>{index} $$raw$$</math>"))
            })
            .collect();
        let expected: Vec<_> = results.iter().flatten().cloned().collect();
        let plan = preflight.into_plan(results).unwrap();
        assert_eq!(visits, visited);
        assert_eq!(plan.html_blocks, expected);
        let mut counter = 7;
        for (index, call) in plan.tex.iter().enumerate() {
            match &plan.stash[index] {
                Stashed::Text(text) => {
                    assert_eq!(text.value, format!("\u{2}wzxhzdk:{counter}\u{3}"));
                    assert_eq!(call.html_block, Some(counter - 7));
                    counter += 1;
                }
                Stashed::Node(node) => {
                    assert_eq!(call.html_block, None);
                    let node = &plan.nodes[*node];
                    assert_eq!(node.tag, "span");
                    assert_eq!(node.attributes, [("class".into(), "tex-error".into())]);
                    let text = node.text.as_ref().unwrap();
                    assert_eq!(text.value, format!("$${}$$", call.body));
                    assert!(text.atomic);
                }
            }
        }
        assert_eq!(counter - 7, expected.len());
    }
}

#[test]
fn keeps_entity_and_tex_stash_order_across_paragraphs_and_tails() {
    let mut nodes = paragraph("$$one$$ &amp;");
    nodes[1].tail = Some(Text {
        value: "$$tail$$ &lt;".into(),
        atomic: false,
    });
    nodes[0].children.push(2);
    nodes.push(node("p", Some("$$two$$ &gt;"), vec![]));
    let checked = preflight(
        nodes,
        &[pattern(22), pattern(20)],
        vec!["outer".into()],
        inputs(),
        |_, _| panic!("native handlers do not use the guard"),
    )
    .unwrap();
    assert_eq!(checked.calls(), ["one", "tail", "two"]);
    let plan = checked
        .into_plan(vec![Some(String::new()), None, Some("<b>two</b>".into())])
        .unwrap();
    assert_eq!(
        plan.html_blocks,
        ["", "&amp;", "&lt;", "<b>two</b>", "&gt;"]
    );
    let placeholders: Vec<_> = plan
        .stash
        .iter()
        .filter_map(|item| match item {
            Stashed::Text(text) => Some(text.value.as_str()),
            _ => None,
        })
        .collect();
    assert_eq!(
        placeholders,
        (7..12)
            .map(|counter| format!("\u{2}wzxhzdk:{counter}\u{3}"))
            .collect::<Vec<_>>()
    );
}

#[test]
fn accounts_for_result_dependent_link_destinations_and_titles() {
    for (success, expected) in [(false, "$$α$$"), (true, "\u{2}wzxhzdk:7\u{3}")] {
        let checked = preflight(
            paragraph("[label](https://example.com/$$α$$ \"$$α$$\")"),
            &[pattern(22), link()],
            vec![],
            inputs(),
            |_, _| panic!("native link"),
        )
        .unwrap();
        assert_eq!(checked.calls(), ["α", "α"]);
        let plan = checked
            .into_plan(vec![success.then(|| "first".into()), None])
            .unwrap();
        let link = plan.nodes.iter().find(|node| node.tag == "a").unwrap();
        assert_eq!(
            link.attributes,
            [
                ("href".into(), format!("https://example.com/{expected}")),
                ("title".into(), "$$α$$".into())
            ]
        );
    }
}

#[test]
fn rejects_a_failure_branch_that_requires_reference_url_validation() {
    assert!(
        preflight(
            paragraph("[label](https://$$é$$)"),
            &[pattern(22), link()],
            vec![],
            inputs(),
            |_, _| Some(()),
        )
        .is_none()
    );
}

#[test]
fn rejects_late_unsupported_handlers_before_any_results_are_requested() {
    let mut nodes = paragraph("$$first$$ &amp;");
    nodes[0].children.push(2);
    nodes.push(node("p", Some("unsupported"), vec![]));
    let mut guards = Vec::new();
    assert!(
        preflight(
            nodes,
            &[pattern(22), pattern(20), pattern(0)],
            vec![],
            inputs(),
            |_, data| {
                guards.push(data.to_owned());
                (!data.contains("unsupported")).then_some(())
            },
        )
        .is_none()
    );
    assert_eq!(guards.len(), 2);
    assert!(guards[0].contains("klzzwxh"));
    assert_eq!(guards[1], "unsupported");
}

#[test]
fn keeps_registry_precedence_atomic_text_and_ancestor_exclusions() {
    let mut tex = pattern(22);
    tex.excludes = vec!["pre".into()];
    let checked = preflight(
        paragraph("`$$code$$` **$$bold$$** $$plain$$"),
        &[pattern(1), pattern(3), tex],
        vec![],
        inputs(),
        |_, _| Some(()),
    )
    .unwrap();
    assert_eq!(checked.calls(), ["bold", "plain"]);
    let plan = checked.into_plan(vec![None, Some("raw".into())]).unwrap();
    assert!(plan.nodes.iter().any(|node| node.tag == "strong"));
    assert!(
        plan.nodes
            .iter()
            .any(|node| { node.tag == "code" && node.text.as_ref().unwrap().value == "$$code$$" })
    );
    let mut nodes = paragraph("$$excluded$$");
    nodes[0].tag = "pre".into();
    nodes[0].lower_tag = "pre".into();
    let mut tex = pattern(22);
    tex.excludes = vec!["pre".into()];
    assert!(
        preflight(nodes, &[tex], vec![], inputs(), |_, _| None)
            .unwrap()
            .calls()
            .is_empty()
    );
    let mut nodes = paragraph("$$atomic$$");
    nodes[1].text.as_mut().unwrap().atomic = true;
    assert!(
        preflight(nodes, &[pattern(22)], vec![], inputs(), |_, _| None)
            .unwrap()
            .calls()
            .is_empty()
    );
}

#[test]
fn rejects_large_graphs_excessive_calls_cycles_and_counter_overflow() {
    for nodes in [
        paragraph("$$a$$ $$b$$ $$c$$ $$d$$ $$e$$ $$f$$ $$g$$"),
        paragraph(&"x".repeat(8 * 1024 * 1024)),
        vec![node("div", None, vec![0])],
        vec![node("div", None, vec![1])],
        vec![node("div", None, vec![0; 100_001])],
    ] {
        assert!(preflight(nodes, &[pattern(22)], vec![], inputs(), |_, _| None).is_none());
    }
    assert!(
        preflight(
            paragraph("$$one$$ $$two$$"),
            &[pattern(22)],
            vec![],
            Inputs {
                html_counter: Some(usize::MAX),
                ..Inputs::default()
            },
            |_, _| None,
        )
        .is_none()
    );
    assert!(
        preflight(
            paragraph("$$one$$"),
            &[pattern(22)],
            vec![],
            Inputs::default(),
            |_, _| None,
        )
        .is_none()
    );
}

#[test]
fn refuses_result_count_errors_and_leaves_ordinary_planners_closed_to_tex() {
    let checked = preflight(
        paragraph("$$one$$"),
        &[pattern(22)],
        vec![],
        inputs(),
        |_, _| None,
    )
    .unwrap();
    assert_eq!(
        checked.into_plan(vec![]).err(),
        Some(TexResultCountError {
            expected: 1,
            actual: 0
        })
    );
    assert!(
        run_with_inputs(
            paragraph("$$one$$"),
            &[pattern(22)],
            vec![],
            inputs(),
            |_, _| None,
        )
        .is_none()
    );
}

#[test]
fn bounds_retained_text_and_graph_items_across_all_outcomes() {
    let source = format!(
        "$$a$$ $$b$$ $$c$$ $$d$$ $$e$$ $$f$$ {}",
        "x".repeat(150_000)
    );
    assert!(
        preflight(
            paragraph(&source),
            &[pattern(22)],
            vec![],
            inputs(),
            |_, _| None
        )
        .is_none()
    );
    let mut nodes = paragraph("$$a$$ $$b$$ $$c$$ $$d$$ $$e$$ $$f$$");
    for _ in 0..1000 {
        let index = nodes.len();
        nodes[0].children.push(index);
        nodes.push(node("p", Some("small"), vec![]));
    }
    assert!(preflight(nodes, &[pattern(22)], vec![], inputs(), |_, _| None).is_none());
}
