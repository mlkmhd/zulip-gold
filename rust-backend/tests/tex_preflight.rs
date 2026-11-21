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

#[test]
fn selects_every_prefix_of_six_results_without_reinvoking_guards() {
    let bodies = ["a", "a", "α", "<&>", "fifth", "last"];
    for prefix in 0..=6 {
        for mask in 0..1 << prefix {
            let mut visits = 0;
            let checked = preflight(
                paragraph("&amp; $$a$$ $$a$$ $$α$$ $$<&>$$ $$fifth$$ $$last$$"),
                &[pattern(20), pattern(22), pattern(0)],
                vec!["outer".into()],
                inputs(),
                |_, _| {
                    visits += 1;
                    Some(())
                },
            )
            .unwrap();
            let visited = visits;
            let results: Vec<_> = (0..prefix)
                .map(|index| (mask & (1 << index) != 0).then(|| format!("<math>{index}</math>")))
                .collect();
            let mut expected = vec!["&amp;".to_owned()];
            expected.extend(results.iter().flatten().cloned());
            let mut progress = checked.begin();
            for result in results {
                progress = progress.next_call.unwrap().complete(result);
            }
            assert_eq!(visits, visited);
            assert_eq!(
                progress.next_call.as_ref().map(|pending| pending.body()),
                bodies.get(prefix).copied()
            );
            assert_eq!(progress.state.tex.len(), prefix);
            assert_eq!(progress.state.html_blocks, expected);
            for (index, call) in progress.state.tex.iter().enumerate() {
                assert_eq!(call.body, bodies[index]);
                assert_eq!(call.html_block.is_some(), mask & (1 << index) != 0);
            }
        }
    }
}

#[test]
fn first_checkpoint_keeps_detached_code_entity_and_cleared_paragraph_text() {
    let checked = preflight(
        paragraph("`code` &amp; $$first$$"),
        &[pattern(1), pattern(20), pattern(22)],
        vec!["outer".into()],
        inputs(),
        |_, _| panic!("native"),
    )
    .unwrap();
    let progress = checked.begin();
    let state = progress.state;
    assert_eq!(
        progress.next_call.as_ref().map(|pending| pending.body()),
        Some("first")
    );
    assert!(state.registry_used);
    assert!(state.nodes[1].text.is_none());
    assert!(state.nodes[1].children.is_empty());
    assert_eq!(state.parent_order, [1]);
    assert_eq!(state.parents, [None, Some(0), None]);
    assert_eq!(state.ancestors, ["outer", "div", "p"]);
    assert_eq!(state.html_blocks, ["&amp;"]);
    assert!(matches!(state.stash[0], Stashed::Node(2)));
    assert_eq!(state.nodes[2].text.as_ref().unwrap().value, "code");
    assert!(state.nodes[2].text.as_ref().unwrap().atomic);
    let Stashed::Text(entity) = &state.stash[1] else {
        panic!("entity placeholder");
    };
    assert_eq!(entity.value, "\u{2}wzxhzdk:7\u{3}");
    assert!(!entity.atomic);
}

#[test]
fn tail_checkpoint_retains_original_tail_and_completed_text_effects() {
    let mut nodes = paragraph("$$first$$ &amp;");
    nodes[1].tail = Some(Text {
        value: "original $$tail$$ &lt;".into(),
        atomic: false,
    });
    let checked = preflight(
        nodes,
        &[pattern(22), pattern(20)],
        vec!["outer".into()],
        inputs(),
        |_, _| None,
    )
    .unwrap();
    let progress = checked
        .begin()
        .next_call
        .unwrap()
        .complete(Some(String::new()));
    let state = progress.state;
    assert_eq!(
        progress.next_call.as_ref().map(|pending| pending.body()),
        Some("tail")
    );
    assert_eq!(
        state.nodes[1].tail.as_ref().unwrap().value,
        "original $$tail$$ &lt;"
    );
    assert_eq!(
        state.nodes[1].text.as_ref().unwrap().value,
        "\u{2}wzxhzdk:7\u{3} \u{2}wzxhzdk:8\u{3}"
    );
    assert_eq!(state.ancestors, ["outer", "div"]);
    assert_eq!(state.html_blocks, ["", "&amp;"]);
    assert_eq!(state.stash.len(), 2);
    assert_eq!(state.tex.len(), 1);
}

#[test]
fn recursive_checkpoint_keeps_ancestor_and_unstashed_node_state() {
    let checked = preflight(
        paragraph("**$$nested$$** $$outer$$"),
        &[pattern(3), pattern(22)],
        vec!["outer".into()],
        inputs(),
        |_, _| None,
    )
    .unwrap();
    let progress = checked.begin();
    assert_eq!(
        progress.next_call.as_ref().map(|pending| pending.body()),
        Some("nested")
    );
    let state = progress.state;
    assert_eq!(state.ancestors, ["outer", "div", "p", "strong"]);
    assert!(state.nodes[1].text.is_none());
    assert!(state.stash.is_empty());
    assert!(state.nodes[1].children.is_empty());
    assert_eq!(state.nodes[2].tag, "strong");
    assert_eq!(state.nodes[2].text.as_ref().unwrap().value, "$$nested$$");
    assert!(state.parents[2].is_none());
}

#[test]
fn no_tex_progress_completes_and_excess_results_are_protocol_errors() {
    let checked = preflight(
        paragraph("&amp;"),
        &[pattern(20)],
        vec![],
        inputs(),
        |_, _| None,
    )
    .unwrap();
    let progress = checked.begin();
    assert!(progress.next_call.is_none());
    assert_eq!(progress.state.html_blocks, ["&amp;"]);
    let checked = preflight(
        paragraph("$$x$$"),
        &[pattern(22)],
        vec![],
        inputs(),
        |_, _| None,
    )
    .unwrap();
    assert_eq!(
        checked.into_plan(vec![None, None]).err(),
        Some(TexResultCountError {
            expected: 1,
            actual: 2
        })
    );
}

#[test]
fn checkpoints_include_only_prior_notifications_and_ordered_attachment_effects() {
    use _zulip_backend::markdown_inline_mentions::UserPolicy;
    use _zulip_backend::markdown_media::{AudioPolicy, ImageMetadata, MimePolicy};
    use _zulip_backend::markdown_native_inline::{
        Media, MentionData, MentionUserSnapshot, Mentions,
    };

    let mut nodes =
        paragraph("@**first** ![one](/user_uploads/1/first) ![again](/user_uploads/1/first)");
    nodes[0].children.extend([2, 3]);
    nodes.push(node("p", Some("$$between$$"), vec![]));
    nodes.push(node(
        "p",
        Some("@**last** ![last](/user_uploads/1/last)"),
        vec![],
    ));
    let user = |id, name: &str| MentionUserSnapshot {
        id,
        full_name: name.into(),
        is_active: true,
    };
    let mentions = Mentions {
        data: Some(MentionData {
            users_by_name: [
                ("first".into(), Some(user(42, "first"))),
                ("last".into(), Some(user(43, "last"))),
            ]
            .into(),
            users_by_id: [(42, user(42, "first")), (43, user(43, "last"))].into(),
            groups_by_name: Default::default(),
        }),
        user_policy: UserPolicy {
            unicode_decimal: Default::default(),
            max_id_digits: 4300,
        },
    };
    let media = Media {
        realm_host: None,
        audio_previews: None,
        image_previews: Some(
            ["1/first", "1/last"]
                .into_iter()
                .map(|path| {
                    (
                        path.into(),
                        ImageMetadata {
                            width: "1".into(),
                            height: "2".into(),
                            content_type: None,
                        },
                    )
                })
                .collect(),
        ),
        audio_policy: AudioPolicy {
            mime: MimePolicy::default(),
            static_url: "/static/".into(),
            external_uri_scheme: "https://".into(),
            public_upload_root: None,
            camo_uri: String::new(),
            camo_key: None,
        },
    };
    let mut image = link();
    image.kind = 13;
    let checked = preflight(
        nodes,
        &[pattern(17), image, pattern(22)],
        vec![],
        Inputs {
            mentions: Some(&mentions),
            media: Some(&media),
            ..inputs()
        },
        |_, _| None,
    )
    .unwrap();
    let progress = checked.begin();
    assert_eq!(progress.state.mentions.users, [42].into());
    assert_eq!(progress.state.attachment_paths, ["1/first", "1/first"]);
    assert!(progress.state.has_image);
    assert!(progress.state.html_blocks.is_empty());
    let complete = progress.next_call.unwrap().complete(Some("raw".into()));
    assert!(complete.next_call.is_none());
    assert_eq!(complete.state.mentions.users, [42, 43].into());
    assert_eq!(
        complete.state.attachment_paths,
        ["1/first", "1/first", "1/last"]
    );
    assert_eq!(complete.state.html_blocks, ["raw"]);
}

#[test]
fn retained_checkpoints_share_the_whole_certificate_memory_budget() {
    let mut nodes = paragraph("$$a$$ $$b$$ $$c$$ $$d$$ $$e$$ $$f$$");
    nodes[0].children.push(2);
    nodes.push(node("p", Some(&"x".repeat(80_000)), vec![]));
    assert!(preflight(nodes, &[pattern(22)], vec![], inputs(), |_, _| None).is_none());
}

#[test]
fn ancestor_history_finishes_old_lists_and_keeps_nested_calls_on_the_same_list() {
    let mut nodes = paragraph("**$$nested$$** $$outer$$");
    nodes[0].children.push(2);
    nodes.push(node("p", None, vec![3]));
    nodes.push(node("span", Some("$$child$$"), vec![]));
    let checked = preflight(
        nodes,
        &[pattern(3), pattern(22)],
        vec!["outer".into()],
        inputs(),
        |_, _| None,
    )
    .unwrap();
    let first = checked.begin();
    assert!(first.state.ancestor_history.is_empty());
    assert_eq!(first.state.ancestors, ["outer", "div", "p", "strong"]);
    let second = first.next_call.unwrap().complete(None);
    assert!(second.state.ancestor_history.is_empty());
    assert_eq!(second.state.ancestors, ["outer", "div", "p"]);
    let third = second.next_call.unwrap().complete(None);
    assert_eq!(third.next_call.as_ref().unwrap().body(), "child");
    assert_eq!(third.state.ancestor_history[0], ["outer", "div"]);
    assert_eq!(third.state.ancestor_history.len(), 3);
    assert_eq!(third.state.ancestors, ["outer", "div", "div", "p", "span"]);
    let history = third.state.ancestor_history;
    let final_state = third.next_call.unwrap().complete(None).state;
    assert!(final_state.ancestor_history.starts_with(&history));
}

#[test]
fn ordinary_plans_do_not_retain_ancestor_lists() {
    let mut nodes = paragraph("**first**");
    nodes[1].children.push(2);
    nodes.push(node("span", Some("second"), vec![]));
    let state = run_with_inputs(nodes, &[pattern(3)], vec![], inputs(), |_, _| None).unwrap();
    assert!(state.ancestor_history.is_empty());
}

#[test]
fn retained_ancestor_aliases_count_toward_certificate_limits() {
    let mut nodes = paragraph("$$one$$");
    nodes[1].children.push(2);
    nodes.push(node("span", Some("$$two$$"), vec![]));
    assert!(
        preflight(
            nodes,
            &[pattern(22)],
            vec!["parent".into(); 30_000],
            inputs(),
            |_, _| None,
        )
        .is_none()
    );
}
