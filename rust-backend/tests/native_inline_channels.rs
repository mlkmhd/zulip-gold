#![cfg(feature = "native-markdown")]

use _zulip_backend::markdown_links::Policy;
use _zulip_backend::markdown_media::{AudioPolicy, ImageMetadata, MimePolicy};
use _zulip_backend::markdown_native_inline::{
    Channel, Channels, Inputs, Media, Node, Pattern, Stashed, Text, run_with_inputs,
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
            links: None,
        })
        .collect()
}

fn channels() -> Channels {
    Channels {
        names: [
            (
                "dev tools".into(),
                Channel {
                    id: 42,
                    empty_topic_only: false,
                },
            ),
            (
                "empty".into(),
                Channel {
                    id: 17,
                    empty_topic_only: true,
                },
            ),
            (
                "dev tools>t@1".into(),
                Channel {
                    id: 99,
                    empty_topic_only: false,
                },
            ),
        ]
        .into(),
        topic_with: [(("dev tools".into(), "t".into()), 123)].into(),
        empty_topic_name: "<empty & **نام**>".into(),
        unicode_decimal: ['۱', '٢'].into(),
    }
}

#[test]
fn registry_order_selects_message_topic_or_channel_interpretation() {
    let channels = channels();
    for (kinds, class, href) in [
        (
            vec![16, 15, 14],
            "message-link",
            "/#narrow/channel/42-dev-tools/topic/t/near/1",
        ),
        (
            vec![15, 16, 14],
            "stream-topic",
            "/#narrow/channel/42-dev-tools/topic/t.401",
        ),
        (
            vec![14, 15, 16],
            "stream",
            "/#narrow/channel/99-dev-tools.3Et.401",
        ),
    ] {
        let result = run_with_inputs(
            nodes("#**dev tools>t@1**", false),
            &patterns(&kinds),
            vec![],
            Inputs {
                channels: Some(&channels),
                ..Inputs::default()
            },
            |_, _| panic!("guard"),
        )
        .unwrap();
        let node = &result.nodes[result.nodes[1].children[0]];
        assert_eq!(node.attributes[0], ("class".into(), class.into()));
        assert_eq!(node.attributes.last().unwrap().1, href);
        assert!(node.text.as_ref().unwrap().atomic);
    }
}

#[test]
fn rejects_unresolved_and_disallowed_topics_before_accepting_later_links() {
    let channels = channels();
    let result = run_with_inputs(
        nodes("#**hidden>t** #**empty>no** #**dev tools>t**", false),
        &patterns(&[16, 15, 14]),
        vec![],
        Inputs {
            channels: Some(&channels),
            ..Inputs::default()
        },
        |_, _| Some(()),
    )
    .unwrap();
    assert_eq!(
        result.nodes[1].text.as_ref().unwrap().value,
        "#**hidden>t** #**empty>no** "
    );
    assert_eq!(result.nodes[1].children.len(), 1);
    let link = &result.nodes[result.nodes[1].children[0]];
    assert_eq!(
        link.attributes.last().unwrap().1,
        "/#narrow/channel/42-dev-tools/topic/t/with/123"
    );
    assert!(result.attachment_paths.is_empty());
    assert!(!result.has_image);
}

#[test]
fn preserves_empty_topic_child_and_atomic_unicode_message_suffix() {
    let channels = channels();
    let result = run_with_inputs(
        nodes("#**dev tools>@۰۰1** #**empty>@۱٢01**", false),
        &patterns(&[16, 15, 14]),
        vec!["outer".into()],
        Inputs {
            channels: Some(&channels),
            ..Inputs::default()
        },
        |_, _| Some(()),
    )
    .unwrap();
    let paragraph = &result.nodes[1];
    assert_eq!(paragraph.children.len(), 2);
    let topic = &result.nodes[paragraph.children[0]];
    assert_eq!(topic.attributes[0].1, "stream-topic");
    assert!(
        topic
            .attributes
            .last()
            .unwrap()
            .1
            .ends_with("/topic/.40.DB.B0.DB.B01")
    );
    let message = &result.nodes[paragraph.children[1]];
    assert_eq!(
        message.attributes.last().unwrap().1,
        "/#narrow/channel/17-empty/topic//near/۱٢01"
    );
    let empty = &result.nodes[message.children[0]];
    assert_eq!(empty.tag, "em");
    assert_eq!(
        empty.text,
        Some(Text {
            value: channels.empty_topic_name.clone(),
            atomic: false
        })
    );
    assert_eq!(
        empty.tail,
        Some(Text {
            value: " @ 💬".into(),
            atomic: true
        })
    );
}

#[test]
fn keeps_labels_atomic_and_respects_excluded_ancestors() {
    let channels = channels();
    let mut registry = patterns(&[16, 15, 14, 3]);
    for pattern in &mut registry[..3] {
        pattern.excludes = vec!["code".into()];
    }
    let result = run_with_inputs(
        nodes("#**dev tools**", false),
        &registry,
        vec!["code".into()],
        Inputs {
            channels: Some(&channels),
            ..Inputs::default()
        },
        |_, _| Some(()),
    )
    .unwrap();
    assert!(result.stash.iter().all(|entry| match entry {
        Stashed::Node(index) => result.nodes[*index].tag != "a",
        _ => true,
    }));
    let result = run_with_inputs(
        nodes("#**dev tools>**", true),
        &registry,
        vec![],
        Inputs::default(),
        |_, _| panic!("guard"),
    )
    .unwrap();
    assert!(!result.registry_used);
    assert!(result.stash.is_empty());
    assert_eq!(
        result.nodes[1].text.as_ref().unwrap().value,
        "#**dev tools>**"
    );
}

#[test]
fn missing_snapshot_and_late_guard_failure_discard_the_entire_plan() {
    assert!(
        run_with_inputs(
            nodes("#**dev tools**", false),
            &patterns(&[14]),
            vec![],
            Inputs::default(),
            |_, _| Some(())
        )
        .is_none()
    );
    let channels = channels();
    let mut calls = 0;
    assert!(
        run_with_inputs(
            nodes("#**dev tools>** unsupported", false),
            &patterns(&[15, 0]),
            vec![],
            Inputs {
                channels: Some(&channels),
                ..Inputs::default()
            },
            |_, _| {
                calls += 1;
                None
            }
        )
        .is_none()
    );
    assert_eq!(calls, 1);
    assert!(
        run_with_inputs(
            nodes("plain", false),
            &patterns(&[14]),
            vec![],
            Inputs::default(),
            |_, _| Some(())
        )
        .is_some()
    );
}

#[test]
fn bounds_rejected_candidate_scanning_and_localized_output() {
    let mut channels = channels();
    assert!(
        run_with_inputs(
            nodes(&"#**unknown>t** ".repeat(10_000), false),
            &patterns(&[15]),
            vec![],
            Inputs {
                channels: Some(&channels),
                ..Inputs::default()
            },
            |_, _| Some(())
        )
        .is_none()
    );
    channels.empty_topic_name = "x".repeat(8 * 1024 * 1024);
    assert!(
        run_with_inputs(
            nodes("#**empty>** #**empty>**", false),
            &patterns(&[15]),
            vec![],
            Inputs {
                channels: Some(&channels),
                ..Inputs::default()
            },
            |_, _| Some(())
        )
        .is_none()
    );
}

#[test]
fn channels_and_media_share_one_plan_and_discard_it_on_late_failure() {
    let channels = channels();
    let media = Media {
        realm_host: None,
        audio_previews: None,
        image_previews: Some(
            [(
                "1/image".into(),
                ImageMetadata {
                    width: "1".into(),
                    height: "2".into(),
                    content_type: None,
                },
            )]
            .into(),
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
    let mut registry = patterns(&[15, 13]);
    registry[1].links = Some(Policy {
        allowed_schemes: ["https".into()].into(),
        netloc_schemes: ["https".into()].into(),
        enable_file_links: false,
        realm_url: None,
    });
    let text = "#**dev tools>t** ![image](/user_uploads/1/image)";
    let result = run_with_inputs(
        nodes(text, false),
        &registry,
        vec![],
        Inputs {
            media: Some(&media),
            channels: Some(&channels),
        },
        |_, _| Some(()),
    )
    .unwrap();
    assert!(result.has_image);
    assert_eq!(result.attachment_paths, ["1/image"]);
    let tags: Vec<_> = result.nodes[1]
        .children
        .iter()
        .map(|&index| result.nodes[index].tag.as_str())
        .collect();
    assert_eq!(tags, ["a", "img"]);
    registry.extend(patterns(&[0]));
    assert!(
        run_with_inputs(
            nodes(text, false),
            &registry,
            vec![],
            Inputs {
                media: Some(&media),
                channels: Some(&channels),
            },
            |_, _| None
        )
        .is_none()
    );
}
