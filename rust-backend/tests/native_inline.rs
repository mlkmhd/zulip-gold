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
        emoticons: None,
        unicode_emoji: None,
        links: None,
    }
}

#[test]
fn timestamps_keep_source_atomic_and_escape_invalid_calendar_values() {
    let plan = run(
        paragraph("<time:2024-03-01T00+03:30> <time:1900-02-29> <time:unclosed"),
        &[pattern(19)],
        vec![],
        |_, _| panic!("timestamp parsing is native"),
    )
    .unwrap();
    let [time] = plan.nodes[1].children.as_slice() else {
        panic!("expected one valid time");
    };
    assert_eq!(plan.nodes[*time].tag, "time");
    assert_eq!(
        plan.nodes[*time].attributes,
        [("datetime".into(), "2024-02-29T20:30:00Z".into())]
    );
    assert_eq!(
        plan.nodes[*time].text,
        Some(Text {
            value: "2024-03-01T00+03:30".into(),
            atomic: true
        })
    );
    assert_eq!(
        plan.nodes[*time].tail.as_ref().unwrap().value,
        " &lt;time:1900-02-29&gt; <time:unclosed"
    );
}

#[test]
fn timestamps_discard_the_whole_plan_for_unsupported_iso_syntax() {
    assert!(
        run(
            paragraph("**before** <time:2024-01-01T12Z> <time:2024-W01-1T12:3456>"),
            &[pattern(3), pattern(19)],
            vec![],
            |_, _| panic!("native handler delegates without invoking the host"),
        )
        .is_none()
    );
    let mut excluded = pattern(19);
    excluded.excludes.push("p".into());
    let plan = run(
        paragraph("<time:2024-W01-1>"),
        &[excluded],
        vec![],
        |_, _| None,
    )
    .unwrap();
    assert!(plan.nodes[1].children.is_empty());
    assert_eq!(
        plan.nodes[1].text.as_ref().unwrap().value,
        "<time:2024-W01-1>"
    );
}

#[test]
fn timestamp_scans_share_a_bounded_work_budget() {
    assert!(
        run(
            paragraph(&"<time:2024-01-01> ".repeat(2000)),
            &[pattern(19)],
            vec![],
            |_, _| panic!("timestamp scanning is native"),
        )
        .is_none()
    );
}

fn link_pattern() -> Pattern {
    let mut pattern = pattern(11);
    pattern.links = Some(_zulip_backend::markdown_links::Policy {
        allowed_schemes: vec!["http".to_owned(), "https".to_owned()],
        netloc_schemes: vec!["http".to_owned(), "https".to_owned()],
        enable_file_links: false,
        realm_url: None,
    });
    pattern
}

#[test]
fn links_keep_labels_atomic_and_restore_code_stash_in_destinations() {
    let plan = run(
        paragraph("[**label**](https://example.com/`path`) **after**"),
        &[pattern(1), link_pattern(), pattern(3)],
        vec![],
        |_, _| panic!("all handlers are native"),
    )
    .unwrap();
    let [link, strong] = plan.nodes[1].children.as_slice() else {
        panic!("expected link and strong");
    };
    assert_eq!(
        plan.nodes[*link].attributes,
        [("href".to_owned(), "https://example.com/path".to_owned())]
    );
    assert_eq!(
        plan.nodes[*link].text,
        Some(Text {
            value: "**label**".to_owned(),
            atomic: true
        })
    );
    assert_eq!(plan.nodes[*strong].text.as_ref().unwrap().value, "after");
}

#[test]
fn link_search_continues_after_a_rejected_or_incomplete_candidate() {
    let plan = run(
        paragraph("[bad](javascript:x) [incomplete [good](example.com)"),
        &[link_pattern()],
        vec![],
        |_, _| panic!("all handlers are native"),
    )
    .unwrap();
    assert_eq!(
        plan.nodes[1].text.as_ref().unwrap().value,
        "[bad](javascript:x) [incomplete "
    );
    let [link] = plan.nodes[1].children.as_slice() else {
        panic!("expected link")
    };
    assert_eq!(plan.nodes[*link].text.as_ref().unwrap().value, "good");
}

#[test]
fn unsupported_link_validation_discards_earlier_native_nodes() {
    assert!(
        run(
            paragraph("**earlier** [x](https://[::1])"),
            &[pattern(3), link_pattern()],
            vec![],
            |_, _| Some(()),
        )
        .is_none()
    );
}

#[test]
fn incomplete_link_candidates_share_the_native_work_budget() {
    assert!(
        run(
            paragraph(&"[".repeat(20_000)),
            &[link_pattern()],
            vec![],
            |_, _| panic!("all handlers are native"),
        )
        .is_none()
    );
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
            emoticons: None,
            unicode_emoji: None,
            links: None,
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
        emoticons: None,
        unicode_emoji: None,
        links: None,
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

fn emoticon_pattern() -> Pattern {
    use _zulip_backend::markdown_native_inline::Emoticons;
    Pattern {
        kind: 9,
        excludes: vec![],
        emoji: None,
        unicode_emoji: None,
        links: None,
        emoticons: Some(Emoticons {
            conversions: vec![
                (":)".into(), ":slight_smile:".into()),
                (":D".into(), ":smile:".into()),
            ],
            unicode: [
                ("slight_smile".into(), "1f642".into()),
                ("smile".into(), "1f604".into()),
            ]
            .into(),
        }),
    }
}

#[test]
fn translates_emoticons_with_ascii_terminal_boundaries() {
    for boundary in [
        "", " ", ",", ".", ";", "?", "!", "(", ")", "[", "]", "\"", "'", "\n", "\t",
    ] {
        let source = format!("{boundary}:){boundary}");
        let plan = run(paragraph(&source), &[emoticon_pattern()], vec![], |_, _| {
            Some(())
        })
        .unwrap();
        let children = &plan.nodes[1].children;
        assert_eq!(children.len(), 1, "{source:?}");
        let emoji = &plan.nodes[children[0]];
        assert_eq!(emoji.text.as_ref().unwrap().value, ":slight_smile:");
        assert!(emoji.text.as_ref().unwrap().atomic);
        assert_eq!(emoji.attributes[0].1, "emoji emoji-1f642");
        assert_eq!(emoji.attributes[1].1, "slight smile");
    }
    for boundary in [
        "a",
        "فارسی",
        "\r",
        "\u{a0}",
        "\u{2028}",
        "_",
        "\\",
        "/",
        ":",
    ] {
        for source in [format!("{boundary}:)"), format!(":){boundary}")] {
            let plan = run(paragraph(&source), &[emoticon_pattern()], vec![], |_, _| {
                Some(())
            })
            .unwrap();
            assert!(plan.stash.is_empty(), "{source:?}");
            assert_eq!(plan.nodes[1].text.as_ref().unwrap().value, source);
        }
    }
}

#[test]
fn preserves_emoticon_replacement_order_and_missing_name_delegation() {
    let mut configured = emoticon_pattern();
    let catalog = configured.emoticons.as_mut().unwrap();
    catalog
        .conversions
        .push(("slight_smile".into(), "smile".into()));
    let plan = run(paragraph(":)"), &[configured], vec![], |_, _| Some(())).unwrap();
    assert_eq!(
        plan.nodes[plan.nodes[1].children[0]]
            .text
            .as_ref()
            .unwrap()
            .value,
        ":smile:"
    );
    let mut invalid = emoticon_pattern();
    invalid.emoticons.as_mut().unwrap().unicode.clear();
    assert!(
        run(
            paragraph("**earlier** :)"),
            &[pattern(3), invalid],
            vec![],
            |_, _| Some(())
        )
        .is_none()
    );
}

#[test]
fn honors_disabled_translation_and_protected_emoticons() {
    let plan = run(paragraph(":)"), &[pattern(9)], vec![], |_, _| Some(())).unwrap();
    assert_eq!(plan.nodes[1].text.as_ref().unwrap().value, ":)");
    assert!(plan.stash.is_empty());
    let plan = run(
        paragraph("`:)` **:D** :)"),
        &[pattern(1), pattern(3), emoticon_pattern()],
        vec![],
        |_, _| Some(()),
    )
    .unwrap();
    let children = &plan.nodes[1].children;
    assert_eq!(children.len(), 3);
    assert_eq!(plan.nodes[children[0]].text.as_ref().unwrap().value, ":)");
    let strong = &plan.nodes[children[1]];
    assert_eq!(
        plan.nodes[strong.children[0]].text.as_ref().unwrap().value,
        ":smile:"
    );
    assert_eq!(
        plan.nodes[children[2]].text.as_ref().unwrap().value,
        ":slight_smile:"
    );
}

fn unicode_emoji_pattern() -> Pattern {
    use _zulip_backend::markdown_unicode_emoji::{REGEX_VERSION, UnicodeEmoji};
    Pattern {
        kind: 10,
        excludes: vec![],
        emoji: None,
        emoticons: None,
        links: None,
        unicode_emoji: Some(UnicodeEmoji {
            regex_version: REGEX_VERSION.to_owned(),
            names: [
                ("1f600".into(), "grinning".into()),
                ("1f469".into(), "woman".into()),
                ("2764".into(), "heart".into()),
                ("0031-20e3".into(), "one".into()),
                ("1f44d-1f3fd".into(), "thumbs_up_medium_skin_tone".into()),
                ("1f1fa-1f1f8".into(), "flag_us".into()),
                ("1f469-200d-1f4bb".into(), "woman_technologist".into()),
            ]
            .into(),
        }),
    }
}

#[test]
fn renders_qualified_unicode_sequences_and_preserves_text_presentation() {
    let source = "↔ 1 1\u{20e3} ❤ ❤️ 😀 👍🏽 🇺🇸 👩\u{200d}💻 1\u{fe0f}\u{20e3}";
    let plan = run(
        paragraph(source),
        &[unicode_emoji_pattern()],
        vec![],
        |_, _| panic!("Unicode emoji is native"),
    )
    .unwrap();
    assert_eq!(
        plan.nodes[1].text.as_ref().unwrap().value,
        "↔ 1 1\u{20e3} ❤ "
    );
    let expected = [
        ":heart:",
        ":grinning:",
        ":thumbs_up_medium_skin_tone:",
        ":flag_us:",
        ":woman_technologist:",
        ":one:",
    ];
    assert_eq!(plan.nodes[1].children.len(), expected.len());
    for (child, display) in plan.nodes[1].children.iter().zip(expected) {
        let emoji = &plan.nodes[*child];
        assert_eq!(emoji.tag, "span");
        assert_eq!(emoji.text.as_ref().unwrap().value, display);
        assert!(emoji.text.as_ref().unwrap().atomic);
    }
}

#[test]
fn skips_unknown_unicode_clusters_without_rendering_their_prefixes() {
    for source in ["🙂 😀", "😀\u{200d}😀 😀", "🇦🇧 😀", "🦄 😀"] {
        let plan = run(
            paragraph(source),
            &[unicode_emoji_pattern()],
            vec![],
            |_, _| Some(()),
        )
        .unwrap();
        assert_eq!(plan.nodes[1].children.len(), 1, "{source:?}");
        let prefix = source.strip_suffix('😀').unwrap();
        assert_eq!(plan.nodes[1].text.as_ref().unwrap().value, prefix);
        assert_eq!(plan.stash.len(), 1);
    }
}

#[test]
fn regional_pair_stops_before_a_following_zwj() {
    let plan = run(
        paragraph("🇺🇸\u{200d}👩"),
        &[unicode_emoji_pattern()],
        vec![],
        |_, _| Some(()),
    )
    .unwrap();
    let children = &plan.nodes[1].children;
    assert_eq!(children.len(), 2);
    assert_eq!(
        plan.nodes[children[0]].text.as_ref().unwrap().value,
        ":flag_us:"
    );
    assert_eq!(
        plan.nodes[children[0]].tail.as_ref().unwrap().value,
        "\u{200d}"
    );
    assert_eq!(
        plan.nodes[children[1]].text.as_ref().unwrap().value,
        ":woman:"
    );
}

#[test]
fn protects_unicode_code_and_rejects_other_property_versions() {
    let plan = run(
        paragraph("`😀` **😀**"),
        &[pattern(1), pattern(3), unicode_emoji_pattern()],
        vec![],
        |_, _| Some(()),
    )
    .unwrap();
    let children = &plan.nodes[1].children;
    assert_eq!(plan.nodes[children[0]].text.as_ref().unwrap().value, "😀");
    let strong = &plan.nodes[children[1]];
    assert_eq!(
        plan.nodes[strong.children[0]].text.as_ref().unwrap().value,
        ":grinning:"
    );
    let mut incompatible = unicode_emoji_pattern();
    incompatible.unicode_emoji.as_mut().unwrap().regex_version = "different".into();
    assert!(
        run(
            paragraph("**earlier** 😀"),
            &[pattern(3), incompatible],
            vec![],
            |_, _| panic!("version preflight precedes handlers")
        )
        .is_none()
    );
    assert!(run(paragraph("plain"), &[pattern(10)], vec![], |_, _| Some(())).is_none());
}
