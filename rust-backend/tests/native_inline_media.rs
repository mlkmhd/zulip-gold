#![cfg(feature = "native-markdown")]

use std::collections::{HashMap, HashSet};

use _zulip_backend::markdown_links::Policy;
use _zulip_backend::markdown_media::{AudioPolicy, ImageMetadata, MimePolicy};
use _zulip_backend::markdown_native_inline::{Media, Node, Pattern, Stashed, Text, run_with_media};

fn nodes(text: &str) -> Vec<Node> {
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
                atomic: false,
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
            links: Some(Policy {
                allowed_schemes: ["http", "https"].map(str::to_owned).into(),
                netloc_schemes: ["http", "https"].map(str::to_owned).into(),
                enable_file_links: false,
                realm_url: None,
            }),
        })
        .collect()
}

fn media() -> Media {
    Media {
        realm_host: Some("chat.example".into()),
        audio_previews: Some(HashSet::from(["1/voice".into()])),
        image_previews: Some(HashMap::from([(
            "1/picture".into(),
            ImageMetadata {
                width: "10".into(),
                height: "20".into(),
                content_type: Some("image/png".into()),
            },
        )])),
        audio_policy: AudioPolicy {
            mime: MimePolicy {
                types: HashMap::from([(".mp3".into(), "audio/mpeg".into())]),
                supported_audio_types: HashSet::from(["audio/mpeg".into()]),
                param_schemes: ["", "http", "https"].map(str::to_owned).into(),
                ..MimePolicy::default()
            },
            static_url: "/static/".into(),
            external_uri_scheme: "https://".into(),
            public_upload_root: None,
            camo_uri: "https://camo.example/".into(),
            camo_key: Some("secret".into()),
        },
    }
}

#[test]
fn audio_priority_and_rescanning_preserve_duplicate_attachment_effects() {
    let result = run_with_media(
        nodes("![picture](/user_uploads/1/picture) ![voice](/user_uploads/1/voice)"),
        &patterns(&[11, 12, 13]),
        vec![],
        Some(&media()),
        |_, _| Some(()),
    )
    .unwrap();
    assert_eq!(
        result.attachment_paths,
        ["1/picture", "1/voice", "1/picture", "1/picture"]
    );
    assert!(result.has_image);
    let paragraph = &result.nodes[1];
    assert_eq!(paragraph.children.len(), 2);
    assert_eq!(result.nodes[paragraph.children[0]].tag, "img");
    assert_eq!(result.nodes[paragraph.children[1]].tag, "audio");
    let tags: Vec<_> = result
        .stash
        .iter()
        .map(|stashed| match stashed {
            Stashed::Node(node) => result.nodes[*node].tag.as_str(),
            Stashed::Text(_) => panic!("unexpected text stash"),
        })
        .collect();
    assert_eq!(tags, ["audio", "img"]);
}

#[test]
fn registry_order_controls_media_selection_and_side_effects() {
    let mut media = media();
    media
        .audio_previews
        .as_mut()
        .unwrap()
        .insert("1/picture".into());
    for (kinds, tag, image) in [([12, 13], "audio", false), ([13, 12], "img", true)] {
        let result = run_with_media(
            nodes("![x](/user_uploads/1/picture)"),
            &patterns(&kinds),
            vec![],
            Some(&media),
            |_, _| Some(()),
        )
        .unwrap();
        assert_eq!(result.attachment_paths, ["1/picture"]);
        assert_eq!(result.has_image, image);
        assert_eq!(result.nodes[result.nodes[1].children[0]].tag, tag);
    }
}

#[test]
fn rejected_media_keeps_its_effects_without_creating_a_node() {
    let result = run_with_media(
        nodes("![x](/user_uploads/1/unknown)"),
        &patterns(&[12, 13]),
        vec![],
        Some(&media()),
        |_, _| Some(()),
    )
    .unwrap();
    assert_eq!(result.attachment_paths, ["1/unknown", "1/unknown"]);
    assert!(!result.has_image);
    assert!(result.nodes[1].children.is_empty());
    assert!(result.stash.is_empty());
    assert_eq!(
        result.nodes[1].text.as_ref().unwrap().value,
        "![x](/user_uploads/1/unknown)"
    );
}

#[test]
fn later_guard_failure_discards_media_and_its_pending_effects() {
    let mut guarded = false;
    let result = run_with_media(
        nodes("![x](/user_uploads/1/picture) host"),
        &patterns(&[13, 0]),
        vec![],
        Some(&media()),
        |_, _| {
            guarded = true;
            None
        },
    );
    assert!(result.is_none());
    assert!(guarded);
}

#[test]
fn unsupported_later_candidate_and_scan_budget_discard_the_entire_plan() {
    for text in [
        "![x](/user_uploads/1/picture) ![bad](https://例.example/a.mp3)".to_owned(),
        format!("![x](/user_uploads/1/picture) {}", "![".repeat(20_000)),
    ] {
        assert!(
            run_with_media(
                nodes(&text),
                &patterns(&[13]),
                vec![],
                Some(&media()),
                |_, _| Some(())
            )
            .is_none()
        );
    }
}

#[test]
fn atomic_text_and_ancestor_exclusions_leave_metadata_untouched() {
    let mut atomic = nodes("![x](/user_uploads/1/picture)");
    atomic[1].text.as_mut().unwrap().atomic = true;
    let result = run_with_media(
        atomic,
        &patterns(&[12, 13]),
        vec![],
        Some(&media()),
        |_, _| Some(()),
    )
    .unwrap();
    assert!(result.attachment_paths.is_empty());
    assert!(!result.has_image);
    let mut excluded = patterns(&[12, 13]);
    for pattern in &mut excluded {
        pattern.excludes = vec!["outer".into()];
    }
    let result = run_with_media(
        nodes("![x](/user_uploads/1/picture)"),
        &excluded,
        vec!["outer".into()],
        Some(&media()),
        |_, _| Some(()),
    )
    .unwrap();
    assert!(result.attachment_paths.is_empty());
    assert!(!result.has_image);
}

#[test]
fn excessive_media_attributes_discard_the_entire_plan() {
    let mut media = media();
    media
        .image_previews
        .as_mut()
        .unwrap()
        .get_mut("1/picture")
        .unwrap()
        .width = "1".repeat(8 * 1024 * 1024);
    assert!(
        run_with_media(
            nodes("![x](/user_uploads/1/picture)"),
            &patterns(&[13]),
            vec![],
            Some(&media),
            |_, _| Some(())
        )
        .is_none()
    );
}
