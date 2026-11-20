#![cfg(feature = "native-markdown")]

use std::collections::HashMap;

use _zulip_backend::markdown_link_syntax::TextStash;
use _zulip_backend::markdown_links::{Policy, Unsupported};
use _zulip_backend::markdown_media::{ImageMetadata, attachment_path, prepare_image};

fn policy() -> Policy {
    Policy {
        allowed_schemes: ["http", "https", "file", "mailto"]
            .map(str::to_owned)
            .to_vec(),
        netloc_schemes: ["http", "https", "file"].map(str::to_owned).to_vec(),
        enable_file_links: false,
        realm_url: Some("https://chat.example".to_owned()),
    }
}

fn previews() -> HashMap<String, ImageMetadata> {
    HashMap::from([(
        "1/سلام.png".to_owned(),
        ImageMetadata {
            width: "123456789012345678901234567890".to_owned(),
            height: "0".to_owned(),
            content_type: Some("image/png".to_owned()),
        },
    )])
}

#[test]
fn preserves_ordered_attributes_and_pending_attachment_effects() {
    let data = "قبل ![<&**alt**>](/user_uploads/1/سلام.png 'a\nb') tail";
    let result =
        prepare_image(data, "قبل ".len(), None, &policy(), None, Some(&previews())).unwrap();
    assert_eq!(result.attachment_path.as_deref(), Some("1/سلام.png"));
    let image = result.image.unwrap();
    assert_eq!(&data[image.end..], " tail");
    assert_eq!(
        image.attributes,
        [
            ("src", "/static/images/loading/loader-black.svg"),
            ("title", "a b"),
            ("alt", "<&**alt**>"),
            ("data-original-src", "/user_uploads/1/سلام.png"),
            ("class", "inline-image image-loading-placeholder"),
            (
                "data-original-dimensions",
                "123456789012345678901234567890x0"
            ),
            ("data-original-content-type", "image/png"),
        ]
        .map(|(key, value)| (key.to_owned(), value.to_owned()))
    );
}

#[test]
fn local_rewriting_can_reject_an_image_while_recording_its_attachment() {
    for src in [
        "https://chat.example/user_uploads/1/سلام.png",
        "/user_uploads/1/سلام.png?download=1",
        "/user_uploads/1/سلام.png#preview",
        "/user_uploads/1/../other.png",
    ] {
        let data = format!("![x](<{src}>)");
        let result = prepare_image(&data, 0, None, &policy(), None, Some(&previews())).unwrap();
        assert!(result.image.is_none(), "{src}");
        assert_eq!(
            result.attachment_path.as_deref(),
            Some(if src.contains("..") {
                "other.png"
            } else {
                "1/سلام.png"
            })
        );
    }
}

#[test]
fn preview_lookup_uses_raw_suffix_while_attachment_recording_uses_url_path() {
    let mut metadata = previews();
    metadata.insert(
        "1/سلام.png?x#y".to_owned(),
        ImageMetadata {
            width: "5".to_owned(),
            height: "6".to_owned(),
            content_type: None,
        },
    );
    let result = prepare_image(
        "![x](/user_uploads/1/سلام.png?x#y)",
        0,
        None,
        &policy(),
        None,
        Some(&metadata),
    )
    .unwrap();
    assert_eq!(result.attachment_path.as_deref(), Some("1/سلام.png"));
    let image = result.image.unwrap();
    assert_eq!(image.attributes.last().unwrap().1, "5x6");
    assert_eq!(image.attributes[2].1, "/user_uploads/1/سلام.png?x#y");
}

#[test]
fn requires_preview_metadata_without_changing_other_rendering_state() {
    let data = "![x](/user_uploads/1/سلام.png)";
    for previews in [None, Some(HashMap::new())] {
        let result = prepare_image(data, 0, None, &policy(), None, previews.as_ref()).unwrap();
        assert!(result.image.is_none());
        assert_eq!(result.attachment_path.as_deref(), Some("1/سلام.png"));
    }
    for content_type in [None, Some(String::new())] {
        let mut previews = previews();
        previews.get_mut("1/سلام.png").unwrap().content_type = content_type;
        let result = prepare_image(data, 0, None, &policy(), None, Some(&previews)).unwrap();
        let image = result.image.unwrap();
        assert_eq!(image.attributes.len(), 5);
        assert!(!image.attributes.iter().any(|(key, _)| key == "title"));
    }
}

#[test]
fn restores_alt_once_and_destination_twice() {
    let stash = TextStash::from([
        ("0000".to_owned(), "\u{2}klzzwxh:0001\u{3}".to_owned()),
        ("0001".to_owned(), "/user_uploads/1/سلام.png".to_owned()),
    ]);
    let data = "![\u{2}klzzwxh:0000\u{3}](\u{2}klzzwxh:0000\u{3} '')";
    let result = prepare_image(data, 0, Some(&stash), &policy(), None, Some(&previews())).unwrap();
    let image = result.image.unwrap();
    assert_eq!(image.attributes[1], ("title".to_owned(), String::new()));
    assert_eq!(image.attributes[2].1, "\u{2}klzzwxh:0001\u{3}");
    assert_eq!(result.attachment_path.as_deref(), Some("1/سلام.png"));
}

#[test]
fn scheme_rejection_has_no_attachment_effect_and_bad_authorities_delegate() {
    for data in [
        "![x](javascript:/user_uploads/a)",
        "![x](file:///user_uploads/a)",
        "not an image",
        "![unfinished",
    ] {
        let result = prepare_image(data, 0, None, &policy(), None, None).unwrap();
        assert!(result.image.is_none());
        assert!(result.attachment_path.is_none());
    }
    for data in ["![x](http://[bad]/a)", "![x](https://例.example/a)"] {
        assert_eq!(
            prepare_image(data, 0, None, &policy(), None, None),
            Err(Unsupported)
        );
    }
}

#[test]
fn normalizes_attachment_paths_without_normalizing_preview_keys() {
    for (url, expected) in [
        ("user_uploads/1//a.png", Some("1/a.png")),
        ("/user_uploads/1//a.png", Some("1//a.png")),
        ("/user_uploads/1/a.png;", Some("1/a.png")),
        ("/user_uploads/1/..;x", Some(";x")),
        ("/user_uploads/", Some("")),
        ("/../\u{1}/user_uploads/a", Some("a")),
        ("/../http:/user_uploads/a", Some("a")),
        ("https://chat.example/user_uploads/a?x#y", Some("a")),
        ("https://CHAT.example/user_uploads/a", None),
        ("https://elsewhere.example/user_uploads/a", None),
        ("/a/../../other/a", None),
    ] {
        assert_eq!(
            attachment_path(url, Some("chat.example")),
            Ok(expected.map(str::to_owned)),
            "{url:?}"
        );
    }
    assert_eq!(
        attachment_path("http://[::1]/user_uploads/a", None),
        Err(Unsupported)
    );
}
