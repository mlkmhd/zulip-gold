#![cfg(feature = "native-markdown")]

use _zulip_backend::markdown_inline_mentions::UserPolicy;
use _zulip_backend::markdown_links::Policy;
use _zulip_backend::markdown_native_inline::{
    Inputs, MentionData, MentionEffects, MentionGroupSnapshot, MentionUserSnapshot, Mentions, Node,
    Pattern, Text, run_with_inputs,
};

fn nodes(text: &str, atomic: bool) -> Vec<Node> {
    vec![
        Node {
            tag: "div".into(),
            lower_tag: "div".into(),
            text: None,
            tail: None,
            children: vec![1],
            attributes: vec![],
        },
        Node {
            tag: "p".into(),
            lower_tag: "p".into(),
            text: Some(Text {
                value: text.into(),
                atomic,
            }),
            tail: None,
            children: vec![],
            attributes: vec![],
        },
    ]
}

fn patterns(kinds: &[u8]) -> Vec<Pattern> {
    kinds
        .iter()
        .map(|&kind| Pattern {
            kind,
            excludes: vec![],
            emoji: None,
            emoticons: None,
            unicode_emoji: None,
            links: (kind == 11).then(|| Policy {
                allowed_schemes: ["https".into()].into(),
                netloc_schemes: ["https".into()].into(),
                enable_file_links: false,
                realm_url: None,
            }),
        })
        .collect()
}

fn mentions() -> Mentions {
    let user = || MentionUserSnapshot {
        id: 42,
        full_name: "Alice <&>".into(),
        is_active: true,
    };
    Mentions {
        data: Some(MentionData {
            users_by_name: [
                ("alias".into(), Some(user())),
                ("Alias".into(), None),
                ("all".into(), None),
                ("topic".into(), None),
                ("unknown".into(), None),
            ]
            .into(),
            users_by_id: [(42, user())].into(),
            groups_by_name: [
                (
                    "group".into(),
                    Some(MentionGroupSnapshot {
                        id: 15,
                        display_name: "گروه <&**bold**>".into(),
                        deactivated: false,
                    }),
                ),
                ("unknown".into(), None),
            ]
            .into(),
        }),
        user_policy: UserPolicy {
            unicode_decimal: ('۰'..='۹').zip(0..10).collect(),
            max_id_digits: 4300,
        },
    }
}

#[test]
fn plans_atomic_spans_and_accumulates_notification_sets() {
    let mentions = mentions();
    let state = run_with_inputs(
        nodes(
            "@**alias** @**|۰۴۲** @**all** @**topic** @*group* @*group*",
            false,
        ),
        &patterns(&[17, 18]),
        vec!["outer".into()],
        Inputs {
            mentions: Some(&mentions),
            ..Inputs::default()
        },
        |_, _| panic!("no guarded handler"),
    )
    .unwrap();
    assert_eq!(
        state.mentions,
        MentionEffects {
            users: [42].into(),
            groups: [15].into(),
            channel: true,
            topic: true,
        }
    );
    let spans: Vec<_> = state
        .nodes
        .iter()
        .filter(|node| node.tag == "span")
        .collect();
    assert_eq!(spans.len(), 6);
    assert!(spans.iter().all(|node| node.text.as_ref().unwrap().atomic));
    assert_eq!(spans[0].text.as_ref().unwrap().value, "@Alice <&>");
    assert_eq!(spans[4].text.as_ref().unwrap().value, "@گروه <&**bold**>");
    assert!(state.attachment_paths.is_empty() && !state.has_image);
}

#[test]
fn rejects_unknown_or_mismatched_names_then_keeps_scanning() {
    let mentions = mentions();
    let state = run_with_inputs(
        nodes(
            "@**Wrong|42** @**Alias** @*unknown* @**alias** @*group*",
            false,
        ),
        &patterns(&[17, 18]),
        vec![],
        Inputs {
            mentions: Some(&mentions),
            ..Inputs::default()
        },
        |_, _| Some(()),
    )
    .unwrap();
    assert_eq!(state.mentions.users, [42].into());
    assert_eq!(state.mentions.groups, [15].into());
    assert_eq!(
        state.nodes.iter().filter(|node| node.tag == "span").count(),
        2
    );
    assert_eq!(
        state.nodes[1].text.as_ref().unwrap().value,
        "@**Wrong|42** @**Alias** @*unknown* "
    );
}

#[test]
fn silent_and_inactive_mentions_do_not_clear_earlier_notifications() {
    let mut mentions = mentions();
    let data = mentions.data.as_mut().unwrap();
    data.users_by_name.insert(
        "inactive".into(),
        Some(MentionUserSnapshot {
            id: 99,
            full_name: "Inactive".into(),
            is_active: false,
        }),
    );
    data.groups_by_name.insert(
        "old".into(),
        Some(MentionGroupSnapshot {
            id: 99,
            display_name: "Old".into(),
            deactivated: true,
        }),
    );
    let state = run_with_inputs(
        nodes(
            "@**alias** @_**alias** @**inactive** @**topic** @_**topic** @*group* @_*group* @*old*",
            false,
        ),
        &patterns(&[17, 18]),
        vec![],
        Inputs {
            mentions: Some(&mentions),
            ..Inputs::default()
        },
        |_, _| Some(()),
    )
    .unwrap();
    assert_eq!(state.mentions.users, [42].into());
    assert_eq!(state.mentions.groups, [15].into());
    assert!(state.mentions.topic && !state.mentions.channel);
    let values: Vec<_> = state
        .nodes
        .iter()
        .filter(|n| n.tag == "span")
        .map(|n| n.text.as_ref().unwrap().value.as_str())
        .collect();
    assert_eq!(
        values,
        [
            "@Alice <&>",
            "Alice <&>",
            "Inactive",
            "@topic",
            "topic",
            "@گروه <&**bold**>",
            "گروه <&**bold**>",
            "Old"
        ]
    );
}

#[test]
fn missing_context_is_explicit_and_missing_snapshots_delegate() {
    let mut mentions = mentions();
    mentions.data = None;
    let state = run_with_inputs(
        nodes("@**all** @*group*", false),
        &patterns(&[17, 18]),
        vec![],
        Inputs {
            mentions: Some(&mentions),
            ..Inputs::default()
        },
        |_, _| Some(()),
    )
    .unwrap();
    assert_eq!(state.mentions, MentionEffects::default());
    assert_eq!(state.nodes.len(), 2);
    assert!(
        run_with_inputs(
            nodes("@**all**", false),
            &patterns(&[17]),
            vec![],
            Inputs::default(),
            |_, _| Some(())
        )
        .is_none()
    );
    assert!(
        run_with_inputs(
            nodes("plain", false),
            &patterns(&[17, 18]),
            vec![],
            Inputs::default(),
            |_, _| Some(())
        )
        .is_some()
    );
}

#[test]
fn priority_code_atomic_strings_and_ancestors_control_dispatch() {
    let mentions = mentions();
    for kinds in [&[1, 17, 18][..], &[18, 17, 1][..]] {
        let state = run_with_inputs(
            nodes("` @**alias** ` @*group*", false),
            &patterns(kinds),
            vec![],
            Inputs {
                mentions: Some(&mentions),
                ..Inputs::default()
            },
            |_, _| Some(()),
        )
        .unwrap();
        assert_eq!(state.mentions.users.contains(&42), kinds[0] != 1);
        assert_eq!(state.mentions.groups, [15].into());
    }
    for atomic in [false, true] {
        let mut patterns = patterns(&[17, 18]);
        for pattern in &mut patterns {
            pattern.excludes = vec!["code".into()];
        }
        let state = run_with_inputs(
            nodes("@**all** @*group*", atomic),
            &patterns,
            vec!["code".into()],
            Inputs {
                mentions: Some(&mentions),
                ..Inputs::default()
            },
            |_, _| Some(()),
        )
        .unwrap();
        assert_eq!(state.mentions, MentionEffects::default());
        assert_eq!(state.nodes.len(), 2);
    }
    let state = run_with_inputs(
        nodes("@**all**", true),
        &patterns(&[17]),
        vec![],
        Inputs::default(),
        |_, _| Some(()),
    )
    .unwrap();
    assert_eq!(state.mentions, MentionEffects::default());
}

#[test]
fn late_guard_url_or_integer_limit_failure_discards_the_whole_plan() {
    let mut mentions = mentions();
    for (text, kinds) in [
        ("@**alias**", &[17, 0][..]),
        ("@**alias** [x](https://例.example/a)", &[17, 11][..]),
    ] {
        assert!(
            run_with_inputs(
                nodes(text, false),
                &patterns(kinds),
                vec![],
                Inputs {
                    mentions: Some(&mentions),
                    ..Inputs::default()
                },
                |_, _| None
            )
            .is_none()
        );
    }
    mentions.user_policy.max_id_digits = 3;
    assert!(
        run_with_inputs(
            nodes("@**alias** @**|00042**", false),
            &patterns(&[17]),
            vec![],
            Inputs {
                mentions: Some(&mentions),
                ..Inputs::default()
            },
            |_, _| Some(())
        )
        .is_none()
    );
}

#[test]
fn limits_candidate_scanning_and_output_before_returning_metadata() {
    let mut mentions = mentions();
    assert!(
        run_with_inputs(
            nodes(&"@**unknown** ".repeat(10_000), false),
            &patterns(&[17]),
            vec![],
            Inputs {
                mentions: Some(&mentions),
                ..Inputs::default()
            },
            |_, _| Some(())
        )
        .is_none()
    );
    mentions
        .data
        .as_mut()
        .unwrap()
        .groups_by_name
        .get_mut("group")
        .unwrap()
        .as_mut()
        .unwrap()
        .display_name = "x".repeat(8 * 1024 * 1024);
    assert!(
        run_with_inputs(
            nodes("@**all** @*group*", false),
            &patterns(&[17, 18]),
            vec![],
            Inputs {
                mentions: Some(&mentions),
                ..Inputs::default()
            },
            |_, _| Some(())
        )
        .is_none()
    );
}

#[test]
fn missing_name_snapshots_delegate_but_cached_misses_remain_unresolved() {
    let mut mentions = mentions();
    for text in ["@**alias** @**new**", "@**alias** @*new*"] {
        assert!(
            run_with_inputs(
                nodes(text, false),
                &patterns(&[17, 18]),
                vec![],
                Inputs {
                    mentions: Some(&mentions),
                    ..Inputs::default()
                },
                |_, _| Some(())
            )
            .is_none()
        );
    }
    let data = mentions.data.as_mut().unwrap();
    data.users_by_name.insert("new".into(), None);
    data.groups_by_name.insert("new".into(), None);
    let state = run_with_inputs(
        nodes("@**alias** @**new** @*new*", false),
        &patterns(&[17, 18]),
        vec![],
        Inputs {
            mentions: Some(&mentions),
            ..Inputs::default()
        },
        |_, _| Some(()),
    )
    .unwrap();
    assert_eq!(state.mentions.users, [42].into());
    assert!(state.mentions.groups.is_empty());
}
