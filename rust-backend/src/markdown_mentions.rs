use std::borrow::Cow;
use std::collections::{HashMap, HashSet};
use std::ops::Range;

use crate::markdown_mention_syntax::{Kind, find};

fn visit_mentions(content: &str, stars: &str, mut visit: impl FnMut(Range<usize>, &str, bool)) {
    let kind = if stars == "**" {
        Kind::User
    } else {
        Kind::Group
    };
    let mut start = 0;
    while let Some(candidate) = find(content, start, kind).expect("valid mention boundary") {
        start = candidate.range.end;
        visit(candidate.range, &content[candidate.name], candidate.silent);
    }
}

fn visit_user_candidates(content: &str, mut remember: impl FnMut(&str)) -> (bool, bool) {
    let mut topic_wildcard = false;
    let mut channel_wildcard = false;
    visit_mentions(content, "**", |_range, name, _silent| match name {
        "topic" => topic_wildcard = true,
        "all" | "everyone" | "stream" | "channel" => channel_wildcard = true,
        _ => remember(name),
    });
    (topic_wildcard, channel_wildcard)
}

pub(crate) fn possible_user_mentions(content: &str) -> (HashSet<String>, bool, bool) {
    let mut names = HashSet::new();
    let (topic_wildcard, channel_wildcard) = visit_user_candidates(content, |name| {
        names.insert(name.to_owned());
    });
    (names, topic_wildcard, channel_wildcard)
}

#[cfg(any(feature = "python", test))]
pub(crate) fn ordered_possible_user_mentions(content: &str) -> (Vec<String>, bool, bool) {
    let mut names = Vec::new();
    let (topic_wildcard, channel_wildcard) =
        visit_user_candidates(content, |name| names.push(name.to_owned()));
    (names, topic_wildcard, channel_wildcard)
}

#[cfg(test)]
#[test]
fn ordered_scan_keeps_first_encounter_and_duplicate_events() {
    assert_eq!(
        ordered_possible_user_mentions("@**Z** @**topic** @_**ش** @**Z** @**all**"),
        (["Z", "ش", "Z"].map(str::to_owned).into(), true, true),
    );
}

pub(crate) fn possible_group_mentions(content: &str) -> HashMap<String, &'static str> {
    let mut names = HashMap::new();
    visit_mentions(content, "*", |_range, name, silent| {
        let state = names.entry(name.to_owned()).or_insert("silent");
        if !silent {
            *state = "non-silent";
        }
    });
    names
}

fn silence_mentions<'a>(content: &'a str, stars: &str) -> Cow<'a, str> {
    let mut output = String::new();
    let mut copied = 0;
    visit_mentions(content, stars, |range, _name, silent| {
        if !silent {
            output.push_str(&content[copied..range.start + 1]);
            output.push('_');
            copied = range.start + 1;
        }
    });
    if copied == 0 {
        Cow::Borrowed(content)
    } else {
        output.push_str(&content[copied..]);
        Cow::Owned(output)
    }
}

pub(crate) fn silence_quoted_mentions(content: &str) -> String {
    let users = silence_mentions(content, "**");
    silence_mentions(&users, "*").into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_user_candidates_and_case_sensitive_wildcards() {
        assert_eq!(
            possible_user_mentions(
                "x@**hidden** [@**User|123**] \u{1c}@_**topic** <@**channel** \
                 @**Everyone** @**name\nnext** @**incomplete* @_**User|123**"
            ),
            (
                ["User|123", "Everyone", "name\nnext"]
                    .map(str::to_owned)
                    .into(),
                true,
                true
            )
        );
        assert_eq!(
            possible_user_mentions("@**** @**open @**nested**"),
            (["open @"].map(str::to_owned).into(), false, false)
        );
    }

    #[test]
    fn non_silent_group_mentions_override_silent_mentions() {
        assert_eq!(
            possible_group_mentions(
                "@_*support* @*support* @_*support* @*dev* @_*dev* \
                 @**User** [@_*ش\ngroup*] x@*hidden* @**unclosed @*last*"
            ),
            [
                ("support", "non-silent"),
                ("dev", "non-silent"),
                ("ش\ngroup", "silent"),
                ("last", "non-silent")
            ]
            .map(|(name, state)| (name.to_owned(), state))
            .into()
        );
    }

    #[test]
    fn silences_users_before_groups_without_changing_other_text() {
        assert_eq!(
            silence_quoted_mentions(
                "> @**User|123** @*group* @_**all** @**topic** x@**hidden** @**open @*nested*"
            ),
            "> @_**User|123** @_*group* @_**all** @_**topic** x@**hidden** @**open @_*nested*",
        );
        assert_eq!(
            silence_quoted_mentions("\u{1c}@**ش\n📝** [@*group*] @****"),
            "\u{1c}@_**ش\n📝** [@_*group*] @****",
        );
    }
}
