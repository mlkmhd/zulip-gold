#![cfg(feature = "native-markdown")]

use std::collections::{HashMap, HashSet};

use _zulip_backend::markdown_link_syntax::TextStash;
use _zulip_backend::markdown_links::{Policy, Unsupported};
use _zulip_backend::markdown_media::{
    AudioPolicy, MimePolicy, camo_url, is_static_url, mime_type, prepare_audio,
};

fn audio_policy() -> AudioPolicy {
    AudioPolicy {
        mime: MimePolicy {
            suffix_map: HashMap::from([(".mpz".to_owned(), ".mp3.gz".to_owned())]),
            encodings: HashSet::from([".gz".to_owned()]),
            types: HashMap::from([
                (".mp3".to_owned(), "audio/mpeg".to_owned()),
                (".png".to_owned(), "image/png".to_owned()),
            ]),
            supported_audio_types: HashSet::from(["audio/mpeg".to_owned()]),
            param_schemes: ["", "http", "https"].map(str::to_owned).into(),
        },
        static_url: "/static/".to_owned(),
        external_uri_scheme: "https://".to_owned(),
        public_upload_root: None,
        camo_uri: "https://camo.example/".to_owned(),
        camo_key: Some("secret".to_owned()),
    }
}

fn policy() -> Policy {
    Policy {
        allowed_schemes: ["http", "https", "file", "mailto", "data"]
            .map(str::to_owned)
            .to_vec(),
        netloc_schemes: ["http", "https", "file"].map(str::to_owned).to_vec(),
        enable_file_links: false,
        realm_url: None,
    }
}

#[test]
fn prepares_external_audio_with_camo_and_ordered_attributes() {
    let data = "قبل ![  <&**فارسی**> \n](https://elsewhere.example/a.mp3) tail";
    let outcome = prepare_audio(
        data,
        "قبل ".len(),
        None,
        &policy(),
        None,
        None,
        &audio_policy(),
    )
    .unwrap();
    assert!(outcome.attachment_path.is_none());
    let audio = outcome.audio.unwrap();
    assert_eq!(&data[audio.end..], " tail");
    let expected_camo = format!(
        "https://camo.example/824b3e588a46daa78d6413ab71983803790c8815/{}",
        "https://elsewhere.example/a.mp3"
            .bytes()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    );
    assert_eq!(
        audio.attributes,
        vec![
            ("src".to_owned(), expected_camo),
            ("title".to_owned(), "<&**فارسی**>".to_owned()),
            ("controls".to_owned(), "controls".to_owned()),
            ("preload".to_owned(), "metadata".to_owned()),
            (
                "data-original-url".to_owned(),
                "https://elsewhere.example/a.mp3".to_owned()
            ),
        ]
    );
}

#[test]
fn distinguishes_absent_previews_from_empty_and_authorized_uploads() {
    let previews = HashSet::from(["1/upload.png?x#y".to_owned()]);
    for (src, snapshot, accepted, path) in [
        ("/user_uploads/1/a.mp3", None, true, "1/a.mp3"),
        (
            "/user_uploads/1/a.mp3",
            Some(HashSet::new()),
            false,
            "1/a.mp3",
        ),
        (
            "/user_uploads/1/upload.png?x#y",
            Some(previews),
            true,
            "1/upload.png",
        ),
    ] {
        let result = prepare_audio(
            &format!("![](<{src}>)"),
            0,
            None,
            &policy(),
            None,
            snapshot.as_ref(),
            &audio_policy(),
        )
        .unwrap();
        assert_eq!(result.attachment_path.as_deref(), Some(path));
        assert_eq!(result.audio.is_some(), accepted);
        if let Some(audio) = result.audio {
            assert_eq!(audio.attributes[0].1, src);
            assert_eq!(audio.attributes.len(), 3);
        }
    }
}

#[test]
fn local_rewriting_uses_mime_lookup_instead_of_upload_preview_lookup() {
    let mut policy = policy();
    policy.realm_url = Some("https://chat.example".to_owned());
    let outcome = prepare_audio(
        "![x](https://chat.example/user_uploads/1/a.mp3)",
        0,
        None,
        &policy,
        Some("chat.example"),
        Some(&HashSet::new()),
        &audio_policy(),
    )
    .unwrap();
    assert_eq!(outcome.attachment_path.as_deref(), Some("1/a.mp3"));
    assert_eq!(
        outcome.audio.unwrap().attributes[0].1,
        "user_uploads/1/a.mp3"
    );
}

#[test]
fn explicit_titles_reject_before_url_validation_or_attachment_effects() {
    for src in [
        "/user_uploads/1/a.mp3",
        "https://例.example/a.mp3",
        "http://[bad]/a.mp3",
    ] {
        for title in ["''", "'link title'"] {
            let outcome = prepare_audio(
                &format!("![x]({src} {title})"),
                0,
                None,
                &policy(),
                None,
                None,
                &audio_policy(),
            )
            .unwrap();
            assert!(outcome.audio.is_none());
            assert!(outcome.attachment_path.is_none());
        }
    }
}

#[test]
fn restores_destination_twice_but_leaves_label_placeholders_literal() {
    let stash = TextStash::from([
        ("0000".to_owned(), "\u{2}klzzwxh:0001\u{3}".to_owned()),
        ("0001".to_owned(), "/user_uploads/1/a.mp3".to_owned()),
    ]);
    let outcome = prepare_audio(
        "![ \u{2}klzzwxh:0000\u{3} ](\u{2}klzzwxh:0000\u{3})",
        0,
        Some(&stash),
        &policy(),
        None,
        None,
        &audio_policy(),
    )
    .unwrap();
    assert_eq!(outcome.attachment_path.as_deref(), Some("1/a.mp3"));
    assert_eq!(
        outcome.audio.unwrap().attributes[1].1,
        "\u{2}klzzwxh:0000\u{3}"
    );
}

#[test]
fn strict_mime_lookup_preserves_url_parsing_and_case_sensitive_encoding() {
    let policy = audio_policy();
    for (url, expected) in [
        ("https://example/سلام.MP3?x#y", Some("audio/mpeg")),
        ("https://example/a.mp3;param?x#y", Some("audio/mpeg")),
        ("/a.mp3?x", None),
        ("x:/a.mp3?x", None),
        ("https://example/.mp3", None),
        ("https://example/...mp3", None),
        ("https://example/a.MPZ", Some("audio/mpeg")),
        ("https://example/a.mp3.gz", Some("audio/mpeg")),
        ("https://example/a.mp3.GZ", None),
        ("data:audio/mpeg;base64,abc", Some("audio/mpeg")),
        ("data:audio/mpeg", None),
        ("data:;base64,abc", Some("text/plain")),
        ("data:foo=bar/baz,abc", Some("text/plain")),
    ] {
        assert_eq!(
            mime_type(url, &policy.mime),
            Ok(expected.map(str::to_owned)),
            "{url}"
        );
    }
    assert_eq!(
        mime_type("https://example/a.ک", &policy.mime),
        Err(Unsupported)
    );
    let mut policy = audio_policy();
    policy
        .mime
        .suffix_map
        .insert(".mp3".to_owned(), ".mp3".to_owned());
    assert_eq!(
        mime_type("https://example/a.mp3", &policy.mime),
        Err(Unsupported)
    );
}

#[test]
fn static_exemptions_compare_netloc_scheme_and_public_upload_prefix() {
    let mut policy = audio_policy();
    policy.static_url = "https://static.example".to_owned();
    policy.public_upload_root = Some("https://uploads.example/base/".to_owned());
    for (url, expected) in [
        ("https://static.example/a.mp3", true),
        ("https://static.example.evil/a.mp3", false),
        ("https://chat.example/a.mp3", true),
        ("http://chat.example/a.mp3", false),
        ("https://CHAT.example/a.mp3", false),
        ("/user_uploads/a.mp3", true),
        ("relative/a.mp3", true),
        ("//chat.example/a.mp3", false),
        ("https://uploads.example/base/a.mp3", true),
        ("https://uploads.example/base.evil/a.mp3", false),
    ] {
        assert_eq!(
            is_static_url(url, Some("chat.example"), &policy),
            Ok(expected),
            "{url}"
        );
    }
    policy.public_upload_root = Some("https://uploads.example/base".to_owned());
    assert_eq!(
        is_static_url("https://other.example/a", None, &policy),
        Err(Unsupported)
    );
    assert_eq!(
        is_static_url("https://例.example/a", None, &policy),
        Err(Unsupported)
    );
}

#[test]
fn camo_matches_long_utf8_keys_and_keeps_disabled_and_missing_keys_distinct() {
    let mut policy = audio_policy();
    policy.camo_key = Some("ک".repeat(40));
    let encoded = camo_url("https://elsewhere.example/سلام.mp3", &policy).unwrap();
    assert!(encoded.starts_with("https://camo.example/6ea6a71053cc970d6a1537be13533c45bdb433c8/"));
    assert!(encoded.ends_with("2fd8b3d984d8a7d9852e6d7033"));
    policy.camo_key = None;
    assert_eq!(camo_url("url", &policy), Err(Unsupported));
    policy.camo_uri.clear();
    assert_eq!(camo_url("url", &policy), Ok("url".to_owned()));
}
