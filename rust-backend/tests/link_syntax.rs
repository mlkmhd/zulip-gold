#![cfg(feature = "native-markdown")]

use _zulip_backend::markdown_link_syntax::{
    Link, LinkText, ParseError, TextStash, parse, parse_image, prepare_text, unescape,
};

fn check(data: &str, label: &str, href: &str, title: Option<&str>, remaining: &str) {
    let parsed = parse(data, 0).unwrap().unwrap();
    assert_eq!(&data[parsed.text], label);
    assert_eq!(&data[parsed.href], href);
    assert_eq!(parsed.title.map(|range| &data[range]), title);
    assert_eq!(&data[parsed.end..], remaining);
}

#[test]
fn parses_nested_labels_and_destinations() {
    check(
        "[a[b]c](https://x/(a(b)c)) tail",
        "a[b]c",
        "https://x/(a(b)c)",
        None,
        " tail",
    );
    check("[]()", "", "", None, "");
    check("[x](  url  )", "x", "url  ", None, "");
}

#[test]
fn parses_angle_destinations_before_generic_parentheses() {
    check(
        "[x]( < a(b c > ' title ' )!",
        "x",
        "a(b c",
        Some(" title "),
        "!",
    );
    check("[x](< >)", "x", "", None, "");
    check("[x](<a<b>)", "x", "<a<b>", None, "");
    check("[x](<a> junk)", "x", "<a> junk", None, "");
}

#[test]
fn preserves_primary_and_secondary_quote_rules() {
    check("[x](url'\"title\")", "x", "url'", Some("title"), "");
    check("[x](url\"title'\")", "x", "url", Some("title'"), "");
    check("[x](url \"a(b)c\"  )", "x", "url ", Some("a(b)c"), "");
    check("[x](url \"title\"\t)", "x", "url \"title\"\t", None, "");
}

#[test]
fn retains_backup_end_when_a_title_is_unclosed() {
    check(
        "[x](link\"notitle) tail",
        "x",
        "link\"notitle",
        None,
        " tail",
    );
    check(
        "[x]((a)\"unclosed) tail",
        "x",
        "(a)\"unclosed",
        None,
        " tail",
    );
    assert_eq!(parse("[x](url\"(tail", 0), Err(ParseError::InvalidEnd));
}

#[test]
fn uses_byte_offsets_for_multibyte_labels_and_titles() {
    let data = "پیش [دنیا [🌍]](\u{2003}مسیر \"عنوان 🌍\") پس";
    let parsed = parse(data, "پیش ".len()).unwrap().unwrap();
    assert_eq!(&data[parsed.text], "دنیا [🌍]");
    assert_eq!(&data[parsed.href], "مسیر ");
    assert_eq!(parsed.title.map(|r| &data[r]), Some("عنوان 🌍"));
    assert_eq!(&data[parsed.end..], " پس");
}

#[test]
fn recognizes_python_whitespace_around_destinations() {
    for space in [
        '\u{1c}', '\u{85}', '\u{a0}', '\u{1680}', '\u{2003}', '\u{2028}', '\u{3000}',
    ] {
        let data = format!("[x]({space}<{space}url{space}>{space}'title'{space})");
        let parsed = parse(&data, 0).unwrap().unwrap();
        assert_eq!(&data[parsed.href], "url");
        assert_eq!(parsed.title.map(|r| &data[r]), Some("title"));
        assert_eq!(parsed.end, data.len());
    }
}

#[test]
fn leaves_placeholders_and_unsafe_destinations_for_the_caller() {
    check(
        "[\u{2}klzzwxh:0000\u{3}](javascript:alert(1) '\u{2}34\u{3}')",
        "\u{2}klzzwxh:0000\u{3}",
        "javascript:alert(1) ",
        Some("\u{2}34\u{3}"),
        "",
    );
    check("[x](a\\(b\\))", "x", "a\\(b\\)", None, "");
}

#[test]
fn rejects_images_incomplete_links_and_invalid_offsets() {
    for data in ["![x](url)", "[x] (url)", "[x](url", "[[x](url)", "[x]", ""] {
        assert_eq!(parse(data, 0), Ok(None), "{data:?}");
    }
    assert_eq!(parse("![x](url)", 1), Ok(None));
    assert_eq!(parse("🌍[x](url)", 1), Ok(None));
    assert_eq!(parse("[x](url)", usize::MAX), Ok(None));
}

#[test]
fn parses_shared_image_and_audio_syntax_without_selecting_media_types() {
    for data in [
        "![🌍 [alt]](<upload path> 'title') tail",
        "!![🌍 [alt]](<upload path> 'title') tail",
    ] {
        let start = data.find("![").unwrap();
        let parsed = parse_image(data, start).unwrap().unwrap();
        assert_eq!(&data[parsed.text], "🌍 [alt]");
        assert_eq!(&data[parsed.href], "upload path");
        assert_eq!(parsed.title.map(|r| &data[r]), Some("title"));
        assert_eq!(&data[parsed.end..], " tail");
        assert_eq!(parse(data, start + 1), Ok(None));
    }
}

#[test]
fn image_openers_retain_invalid_end_and_offset_handling() {
    assert_eq!(
        parse_image("![x](url\"(tail", 0),
        Err(ParseError::InvalidEnd)
    );
    for (data, start) in [
        ("[x](url)", 0),
        ("![x](url)", 1),
        ("🌍![x](url)", 1),
        ("![x](url)", usize::MAX),
    ] {
        assert_eq!(parse_image(data, start), Ok(None));
    }
}

#[test]
fn restores_stash_identifiers_verbatim_and_only_once() {
    let stash = TextStash::from([
        ("0000".to_owned(), "\u{2}klzzwxh:0001\u{3}".to_owned()),
        ("0001".to_owned(), "دنیا 🌍".to_owned()),
        ("1".to_owned(), "different key".to_owned()),
    ]);
    assert_eq!(
        unescape(
            "\u{2}klzzwxh:0000\u{3} / \u{2}klzzwxh:0001\u{3} / \u{2}klzzwxh:1\u{3}",
            Some(&stash)
        ),
        "\u{2}klzzwxh:0001\u{3} / دنیا 🌍 / different key"
    );
}

#[test]
fn distinguishes_missing_treeprocessor_from_empty_stash() {
    let input = "before \u{2}klzzwxh:0000\u{3} after";
    assert_eq!(unescape(input, None), input);
    assert_eq!(unescape(input, Some(&TextStash::new())), "before  after");
}

#[test]
fn preserves_malformed_and_non_ascii_placeholders() {
    let input = "\u{2}klzzwxh:bad \u{2}klzzwxh:0042\u{3} \u{2}klzzwxh:۴۲\u{3} \u{2}42\u{3}";
    assert_eq!(
        unescape(input, Some(&TextStash::new())),
        "\u{2}klzzwxh:bad  \u{2}klzzwxh:۴۲\u{3} \u{2}42\u{3}"
    );
}

#[test]
fn normalizes_title_whitespace_after_substitution_and_dequoting() {
    let data = "[label]( \u{2}klzzwxh:0000\u{3} '\u{1c}\u{2}klzzwxh:0001\u{3}\u{85}')";
    let stash = TextStash::from([
        ("0000".to_owned(), "\u{2003}url\u{2003}".to_owned()),
        ("0001".to_owned(), "\" a\n\tb\u{a0} \"".to_owned()),
    ]);
    let link = parse(data, 0).unwrap().unwrap();
    assert_eq!(
        prepare_text(data, &link, Some(&stash)),
        Some(LinkText {
            href: "url".to_owned(),
            title: Some(" a  b  ".to_owned()),
        })
    );
    assert_eq!(&data[link.text], "label");
}

#[test]
fn preserves_absent_empty_and_single_quote_titles() {
    for (data, expected) in [
        ("[x](url)", None),
        ("[x](url '')", Some("")),
        ("[x](url '\"')", Some("")),
        ("[x](url '\"mismatch')", Some("\"mismatch")),
        ("[x](url '\"\"')", Some("")),
    ] {
        let link = parse(data, 0).unwrap().unwrap();
        assert_eq!(
            prepare_text(data, &link, None).unwrap().title.as_deref(),
            expected,
            "{data:?}"
        );
    }
}

#[test]
fn leaves_restored_whitespace_and_unsafe_destinations_for_sanitization() {
    let stash = TextStash::from([("0000".to_owned(), " \"title\" ".to_owned())]);
    let data = "[x](javascript:alert(1) '\u{2}klzzwxh:0000\u{3}')";
    let link = parse(data, 0).unwrap().unwrap();
    assert_eq!(
        prepare_text(data, &link, Some(&stash)),
        Some(LinkText {
            href: "javascript:alert(1)".to_owned(),
            title: Some(" \"title\" ".to_owned()),
        })
    );
}

#[test]
fn rejects_invalid_public_ranges_without_panicking() {
    let mut link = Link {
        text: 0..0,
        href: 1..2,
        title: None,
        end: 0,
    };
    assert_eq!(prepare_text("🌍", &link, None), None);
    link.href = 0..4;
    link.title = Some(4..usize::MAX);
    assert_eq!(prepare_text("🌍", &link, None), None);
}
