#![cfg(feature = "native-markdown")]

use _zulip_backend::markdown_link_syntax::TextStash;
use _zulip_backend::markdown_links::{Policy, Unsupported, prepare, sanitize};

fn policy() -> Policy {
    Policy {
        allowed_schemes: [
            "http", "https", "ftp", "file", "mailto", "mid", "mms", "nntp",
        ]
        .map(str::to_owned)
        .to_vec(),
        netloc_schemes: ["http", "https", "ftp", "file", "mms", "nntp"]
            .map(str::to_owned)
            .to_vec(),
        enable_file_links: false,
        realm_url: Some("https://chat.example".to_owned()),
    }
}

#[test]
fn sanitizes_urls_with_reference_scheme_and_authority_rules() {
    for (url, expected) in [
        ("", "http://"),
        (
            "example.com/سلام a?x y#z z",
            "http://example.com/سلام%20a?x%20y#z%20z",
        ),
        ("HTTPS://Example.com:bad/a?#", "https://Example.com:bad/a"),
        ("//example.com/a", "http://example.com/a"),
        ("/path?a#b", "/path?a#b"),
        ("///path", "/path"),
        ("#fragment", "#fragment"),
        ("a@b", "mailto:a@b"),
        ("نام@example.com", "mailto:نام@example.com"),
        ("http:relative", "http:///relative"),
        ("mid:relative", "mid:relative"),
        ("http://a\n\t\rb/path", "http://ab/path"),
        ("\u{1}example.com", "http://\u{1}example.com"),
        ("\u{1}http://example.com", "http://example.com"),
        (" http://example.com", "http://%20http://example.com"),
    ] {
        assert_eq!(
            sanitize(url, &policy()),
            Ok(Some(expected.to_owned())),
            "{url:?}"
        );
    }
}

#[test]
fn rejects_disallowed_schemes_and_obeys_live_file_policy() {
    for url in [
        "javascript:alert(1)",
        "Jav\tAScript://x",
        "data:text/html,x",
        "file:///etc/passwd",
    ] {
        assert_eq!(sanitize(url, &policy()), Ok(None));
    }
    let mut policy = policy();
    policy.enable_file_links = true;
    assert_eq!(
        sanitize("FILE:/a b", &policy),
        Ok(Some("file:///a%20b".to_owned()))
    );
    policy.allowed_schemes.clear();
    assert_eq!(sanitize("example.com", &policy), Ok(None));
    assert_eq!(
        sanitize("/relative", &policy),
        Ok(Some("/relative".to_owned()))
    );
    policy.allowed_schemes.push("custom".to_owned());
    assert_eq!(
        sanitize("custom:path", &policy),
        Ok(Some("custom:path".to_owned()))
    );
    policy.netloc_schemes.push("custom".to_owned());
    assert_eq!(
        sanitize("custom:path", &policy),
        Ok(Some("custom:///path".to_owned()))
    );
}

#[test]
fn delegates_authorities_requiring_unicode_or_ip_validation() {
    for url in [
        "https://例.example/x",
        "http://a／b/x",
        "http://[::1]/",
        "http://x]/",
        "javascript://[x]",
    ] {
        assert_eq!(sanitize(url, &policy()), Err(Unsupported), "{url:?}");
    }
    assert_eq!(
        sanitize("https://example.com/例", &policy()),
        Ok(Some("https://example.com/例".to_owned()))
    );
}

#[test]
fn prepares_atomic_labels_and_rewrites_only_matching_local_targets() {
    for (url, expected) in [
        (
            "https://chat.example/#narrow/channel/1",
            "#narrow/channel/1",
        ),
        ("https://chat.example/user_uploads/a", "user_uploads/a"),
        (
            "https://chat.example/ordinary",
            "https://chat.example/ordinary",
        ),
        (
            "https://chat.example.evil/#x",
            "https://chat.example.evil/#x",
        ),
    ] {
        let data = format!("[]({url} '') tail");
        let link = prepare(&data, 0, None, &policy()).unwrap().unwrap();
        assert_eq!(link.href, expected);
        assert_eq!(link.text, expected);
        assert_eq!(link.title.as_deref(), Some(""));
        assert_eq!(&data[link.end..], " tail");
    }
    let link = prepare("[**atomic**](a 'title')", 0, None, &policy())
        .unwrap()
        .unwrap();
    assert_eq!(link.text, "**atomic**");
    assert_eq!(link.title.as_deref(), Some("title"));
}

#[test]
fn unescapes_destinations_twice_and_strips_before_second_substitution() {
    let stash = TextStash::from([
        ("0000".to_owned(), "\u{2}klzzwxh:0001\u{3}".to_owned()),
        ("0001".to_owned(), " example.com ".to_owned()),
    ]);
    let link = prepare("[x](\u{2}klzzwxh:0000\u{3})", 0, Some(&stash), &policy())
        .unwrap()
        .unwrap();
    assert_eq!(link.href, "http://%20example.com%20");
    let mut stash = stash;
    stash.insert("0001".to_owned(), "javascript:alert(1)".to_owned());
    assert_eq!(
        prepare("[x](\u{2}klzzwxh:0000\u{3})", 0, Some(&stash), &policy()),
        Ok(None)
    );
}

#[test]
fn prepares_reply_children_and_preserves_malformed_reply_labels() {
    for (label, expected) in [
        ("Alice | hello | world", Some(("Alice", "hello | world"))),
        ("Alice &#183; hello", Some(("Alice", "hello"))),
        (" | hello", None),
        ("Alice | ", None),
        (
            "Alice &#183; hello | world",
            Some(("Alice &#183; hello", "world")),
        ),
    ] {
        let data =
            format!("[{label}](https://chat.example/#narrow/dm/1/near/123 'zulip-message-reply')");
        let link = prepare(&data, 0, None, &policy()).unwrap().unwrap();
        assert!(link.reply);
        assert_eq!(link.title, None);
        assert_eq!(
            link.reply_parts
                .as_ref()
                .map(|(a, b)| (a.as_str(), b.as_str())),
            expected
        );
        assert_eq!(link.text, if expected.is_some() { "" } else { label });
    }
    for target in [
        "#narrow/channel/1/near/۱۲۳",
        "#narrow/dm/1/near/123/x",
        "#narrow/channel/1/near/",
    ] {
        let data = format!("[Alice | hello]({target} 'zulip-message-reply')");
        let link = prepare(&data, 0, None, &policy()).unwrap().unwrap();
        assert!(!link.reply);
        assert_eq!(link.title.as_deref(), Some("zulip-message-reply"));
    }
}

#[test]
fn distinguishes_syntax_rejection_and_required_delegation() {
    for data in [
        "[x]",
        "[x](javascript:alert(1))",
        "![x](http://example.com)",
    ] {
        assert_eq!(prepare(data, 0, None, &policy()), Ok(None));
    }
    for data in ["[x](http://[bad])", "[x](url\"(tail"] {
        assert_eq!(prepare(data, 0, None, &policy()), Err(Unsupported));
    }
}
