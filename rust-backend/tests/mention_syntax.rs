#![cfg(feature = "native-markdown")]

use _zulip_backend::markdown_mention_syntax::{Candidate, InvalidStart, Kind, find};

#[test]
fn preserves_unicode_names_and_original_byte_offsets() {
    let data = "🌍 @_**نام|42** @*گروه*";
    let user = find(data, 0, Kind::User).unwrap().unwrap();
    assert_eq!(user.range, 5..20);
    assert_eq!(&data[user.name], "نام|42");
    assert!(user.silent);
    let group = find(data, 0, Kind::Group).unwrap().unwrap();
    assert_eq!(&data[group.range], "@*گروه*");
    assert_eq!(&data[group.name], "گروه");
    assert!(!group.silent);
}

#[test]
fn checks_the_boundary_before_the_search_offset() {
    for boundary in ["a", "📝", "*", "\\", "]", ":", "@", "_", ")"] {
        let data = format!("{boundary}@**name** @*group*");
        assert_eq!(find(&data, boundary.len(), Kind::User), Ok(None));
        assert!(find(&data, boundary.len(), Kind::Group).unwrap().is_some());
    }
    for boundary in [
        "", " ", "\u{1c}", "\u{85}", "\u{2003}", "'", "\"", "(", "{", "[", "/", "<",
    ] {
        let data = format!("{boundary}@**name**");
        assert!(find(&data, boundary.len(), Kind::User).unwrap().is_some());
    }
}

#[test]
fn handles_empty_nested_and_overlapping_markers_like_the_reference() {
    for (data, name) in [
        ("@**** @**ok**", "ok"),
        ("@**open @**nested**", "open @"),
        ("@**one* @**two**", "two"),
        ("@_**name\nnext**", "name\nnext"),
        ("@__**wrong** @**ok**", "ok"),
    ] {
        let candidate = find(data, 0, Kind::User).unwrap().unwrap();
        assert_eq!(&data[candidate.name], name, "{data}");
    }
    assert_eq!(find("@**user**", 0, Kind::Group), Ok(None));
    assert_eq!(find("@*group*", 0, Kind::User), Ok(None));
    assert_eq!(find("@**unfinished", 0, Kind::User), Ok(None));
}

#[test]
fn lets_the_caller_resume_after_a_syntactic_match() {
    let data = "@**first** @**second**";
    let first = find(data, 0, Kind::User).unwrap().unwrap();
    let second = find(data, first.range.end, Kind::User).unwrap().unwrap();
    assert_eq!(&data[second.name], "second");
    assert_eq!(find(data, second.range.end, Kind::User), Ok(None));
    assert_eq!(find(data, data.len(), Kind::User), Ok(None));
}

#[test]
fn distinguishes_invalid_utf8_offsets_from_missing_syntax() {
    assert_eq!(find("ش@**x**", 1, Kind::User), Err(InvalidStart));
    assert_eq!(find("", 1, Kind::Group), Err(InvalidStart));
    assert_eq!(find("x", usize::MAX, Kind::User), Err(InvalidStart));
    assert_eq!(
        find("@**x**", 0, Kind::User),
        Ok(Some(Candidate {
            range: 0..6,
            name: 3..4,
            silent: false,
        }))
    );
}
