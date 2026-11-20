#![cfg(feature = "native-markdown")]

use std::collections::HashSet;

use _zulip_backend::markdown_channel_spans::channel_link;
use _zulip_backend::markdown_channel_syntax::{InvalidStart, Kind, find};

#[test]
fn preserves_raw_channel_topic_and_last_message_separator() {
    let data = "🌍 #** نام > a>b@old@۰۰123** after";
    let digits = ['۰'].into();
    let candidate = find(data, 0, Kind::Message, &digits).unwrap().unwrap();
    assert_eq!(&data[candidate.range], "#** نام > a>b@old@۰۰123**");
    assert_eq!(&data[candidate.channel], " نام ");
    assert_eq!(&data[candidate.topic.unwrap()], " a>b@old");
    assert_eq!(&data[candidate.message.unwrap()], "۰۰123");
    let candidate = find(data, 0, Kind::Topic, &digits).unwrap().unwrap();
    assert_eq!(&data[candidate.topic.unwrap()], " a>b@old@۰۰123");
    assert_eq!(candidate.message, None);
    let candidate = find(data, 0, Kind::Channel, &digits).unwrap().unwrap();
    assert_eq!(&data[candidate.channel], " نام > a>b@old@۰۰123");
    assert_eq!(candidate.topic, None);
}

#[test]
fn checks_original_boundary_at_the_resume_offset() {
    let digits = HashSet::new();
    for before in [
        "", "\u{1c}", "\u{85}", "\u{2003}", "'", "\"", "(", "{", "/", "<",
    ] {
        let data = format!("{before}#**channel**");
        assert!(
            find(&data, before.len(), Kind::Channel, &digits)
                .unwrap()
                .is_some()
        );
    }
    for before in ["[", "x", "🌍", "\u{200b}", "#", ")", ">"] {
        let data = format!("{before}#**hidden** #**visible**");
        let candidate = find(&data, before.len(), Kind::Channel, &digits)
            .unwrap()
            .unwrap();
        assert_eq!(&data[candidate.channel], "visible");
    }
    assert_eq!(
        find("🌍#**x**", 1, Kind::Channel, &digits),
        Err(InvalidStart)
    );
    assert_eq!(find("", 1, Kind::Channel, &digits), Err(InvalidStart));
    assert_eq!(find("🌍", 4, Kind::Channel, &digits), Ok(None));
}

#[test]
fn rejects_incomplete_syntax_and_keeps_searching() {
    let digits = HashSet::new();
    for body in [
        "#****",
        "#**bad*name**",
        "#**>topic@1**",
        "#**x>t@**",
        "#**x>t@²**",
        "#**x>t@1\n**",
    ] {
        let data = format!("{body} #**good>@0001**");
        let candidate = find(&data, 0, Kind::Message, &digits).unwrap().unwrap();
        assert_eq!(&data[candidate.channel], "good");
        assert_eq!(&data[candidate.topic.unwrap()], "");
        assert_eq!(&data[candidate.message.unwrap()], "0001");
    }
    assert_eq!(find("#**unclosed", 0, Kind::Channel, &digits), Ok(None));
    assert_eq!(find("#**x>t@۱**", 0, Kind::Message, &digits), Ok(None));
    assert!(
        find("#**x>t@۱**", 0, Kind::Message, &['۱'].into())
            .unwrap()
            .is_some()
    );
}

#[test]
fn lets_the_caller_reject_a_candidate_and_resume_the_same_pattern() {
    let data = "#**unknown>topic** #**known>**";
    let digits = HashSet::new();
    let first = find(data, 0, Kind::Topic, &digits).unwrap().unwrap();
    assert_eq!(&data[first.channel], "unknown");
    let second = find(data, first.range.end, Kind::Topic, &digits)
        .unwrap()
        .unwrap();
    let topic = &data[second.topic.unwrap()];
    let link = channel_link(42, &data[second.channel], Some(topic), Some(99), None);
    assert_eq!(link.text, "#known > ");
    assert!(link.empty_topic);
    assert_eq!(link.tail, None);
    assert_eq!(
        link.attributes[2].1,
        "/#narrow/channel/42-known/topic//with/99"
    );
}

#[test]
fn preserves_atomic_fields_and_leaves_escaping_to_the_serializer() {
    let link = channel_link(
        42,
        "<نام & channel>",
        Some("<topic>&"),
        Some(9),
        Some("۰۰1"),
    );
    assert_eq!(link.attributes[0], ("class", "message-link".to_owned()));
    assert_eq!(link.attributes.len(), 2);
    assert!(
        link.attributes[1]
            .1
            .ends_with("/topic/.3Ctopic.3E.26/near/۰۰1")
    );
    assert_eq!(link.text, "#<نام & channel> > <topic>& @ 💬");
    assert!(!link.empty_topic);
    let link = channel_link(1, "x", Some(""), None, Some("0001"));
    assert_eq!(link.text, "#x > ");
    assert!(link.empty_topic);
    assert_eq!(link.tail, Some(" @ 💬"));
}
