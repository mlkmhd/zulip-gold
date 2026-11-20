#![cfg(feature = "native-markdown")]

use std::cell::Cell;
use std::collections::HashMap;

use _zulip_backend::markdown_inline_mentions::{
    MentionNotification, MentionUser, Unsupported, UserPolicy, UserReference, group_mention_span,
    prepare_user,
};
use _zulip_backend::markdown_mention_syntax::{Kind, find};

fn policy() -> UserPolicy {
    UserPolicy {
        unicode_decimal: ('٠'..='٩')
            .zip(0..10)
            .chain(('۰'..='۹').zip(0..10))
            .collect(),
        max_id_digits: 4300,
    }
}

fn user(is_active: bool) -> MentionUser<'static> {
    MentionUser {
        id: 42,
        full_name: "نام <&**User**>",
        is_active,
    }
}

#[test]
fn resolves_decimal_ids_and_checks_the_exact_canonical_name() {
    for name in [
        "|42",
        "|٠٤۲",
        "|42\n",
        "نام <&**User**>|42",
        "نام <&**User**>|٤٢\n",
    ] {
        let span = prepare_user(name, false, &policy(), |reference| {
            assert_eq!(reference, UserReference::Id(42));
            Some(user(true))
        })
        .unwrap()
        .unwrap();
        assert_eq!(span.text, "@نام <&**User**>");
        assert_eq!(span.notification, Some(MentionNotification::User(42)));
        assert_eq!(span.attributes[0], ("data-user-id", "42".to_owned()));
    }
    for name in ["alias|42", "نام <&**user**>|42", "a|b|42"] {
        assert_eq!(
            prepare_user(name, false, &policy(), |_| Some(user(true))),
            Ok(None)
        );
    }
}

#[test]
fn preserves_name_lookup_for_non_id_syntax() {
    for name in [
        "|",
        "name|",
        "name|42 ",
        "name\n|42",
        "|42\n\n",
        "|²",
        "|+42",
        "|-42",
        "|٤٢x",
    ] {
        let span = prepare_user(name, false, &policy(), |reference| {
            assert_eq!(reference, UserReference::Name(name));
            Some(user(true))
        })
        .unwrap()
        .unwrap();
        assert_eq!(span.text, "@نام <&**User**>");
    }
}

#[test]
fn missing_users_and_out_of_range_ids_cannot_create_mentions() {
    for name in ["|2147483648", "|9999999999999999999999999"] {
        assert_eq!(
            prepare_user(name, false, &policy(), |_| panic!(
                "unrepresentable user ID"
            )),
            Ok(None)
        );
    }
    for (name, id) in [("|0", 0), ("|2147483647", i32::MAX)] {
        assert_eq!(
            prepare_user(name, false, &policy(), |reference| {
                assert_eq!(reference, UserReference::Id(id));
                None
            }),
            Ok(None)
        );
    }
    assert_eq!(
        prepare_user("unknown", false, &policy(), |_| None),
        Ok(None)
    );
}

#[test]
fn preserves_live_digit_limits_before_integer_range_rejection() {
    let mut policy = policy();
    policy.max_id_digits = 3;
    assert_eq!(
        prepare_user("|00042", false, &policy, |_| None),
        Err(Unsupported)
    );
    assert_eq!(
        prepare_user("|٠۰٠42", false, &policy, |_| None),
        Err(Unsupported)
    );
    let span = prepare_user("|042", false, &policy, |_| Some(user(true))).unwrap();
    assert!(span.is_some());
    policy.max_id_digits = 0;
    let name = format!("|{}42", "0".repeat(10000));
    assert!(
        prepare_user(&name, false, &policy, |_| Some(user(true)))
            .unwrap()
            .is_some()
    );
    policy.unicode_decimal.insert('۹', 10);
    assert_eq!(
        prepare_user("|۹", false, &policy, |_| None),
        Err(Unsupported)
    );
}

#[test]
fn wildcard_notification_and_deactivation_do_not_mutate_external_state() {
    for (name, notification) in [
        ("all", MentionNotification::Channel),
        ("everyone", MentionNotification::Channel),
        ("stream", MentionNotification::Channel),
        ("channel", MentionNotification::Channel),
        ("topic", MentionNotification::Topic),
    ] {
        let called = Cell::new(false);
        let span = prepare_user(name, false, &policy(), |reference| {
            assert_eq!(reference, UserReference::Name(name));
            called.set(true);
            Some(user(false))
        })
        .unwrap()
        .unwrap();
        assert!(called.get());
        assert_eq!(span.notification, Some(notification));
        assert_eq!(span.text, format!("@{name}"));
        assert!(
            prepare_user(name, true, &policy(), |_| None)
                .unwrap()
                .unwrap()
                .notification
                .is_none()
        );
    }
    assert_eq!(prepare_user("All", false, &policy(), |_| None), Ok(None));
    for (silent, active) in [(true, true), (false, false), (true, false)] {
        let span = prepare_user("|42", silent, &policy(), |_| Some(user(active)))
            .unwrap()
            .unwrap();
        assert_eq!(span.text, "نام <&**User**>");
        assert_eq!(
            span.attributes[1],
            ("class", "user-mention silent".to_owned())
        );
        assert_eq!(span.notification, None);
    }
    let spans = [
        group_mention_span(15, "<Group & 📝>", false, false),
        group_mention_span(15, "<Group & 📝>", false, true),
    ];
    assert_eq!(spans[0].notification, Some(MentionNotification::Group(15)));
    assert_eq!(spans[1].notification, None);
    assert_eq!(spans[0].text, "@<Group & 📝>");
    assert_eq!(spans[1].text, "<Group & 📝>");
}

#[test]
fn accepts_a_snapshot_without_unicode_decimal_digits() {
    let policy = UserPolicy {
        unicode_decimal: HashMap::new(),
        max_id_digits: 0,
    };
    prepare_user("|٤٢", false, &policy, |reference| {
        assert_eq!(reference, UserReference::Name("|٤٢"));
        None
    })
    .unwrap();
}

#[test]
fn combines_candidate_scanning_with_rejection_and_notification_preparation() {
    let data = "🌍 @**Wrong|42** @_**|42** @**topic**";
    let mut start = 0;
    let mut spans = Vec::new();
    while let Some(candidate) = find(data, start, Kind::User).unwrap() {
        start = candidate.range.end;
        let span = prepare_user(
            &data[candidate.name],
            candidate.silent,
            &policy(),
            |reference| match reference {
                UserReference::Id(42) => Some(user(true)),
                _ => None,
            },
        )
        .unwrap();
        if let Some(span) = span {
            spans.push((span.text, span.notification));
        }
    }
    assert_eq!(
        spans,
        vec![
            ("نام <&**User**>".to_owned(), None),
            ("@topic".to_owned(), Some(MentionNotification::Topic)),
        ]
    );
}
