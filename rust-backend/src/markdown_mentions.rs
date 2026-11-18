use std::collections::{HashMap, HashSet};
use std::ops::Range;

use crate::draft_text::is_python_whitespace;

fn visit_mentions(content: &str, stars: &str, mut visit: impl FnMut(Range<usize>, &str, bool)) {
    let mut mention_end = 0;
    for (start, _) in content.match_indices('@') {
        if start < mention_end {
            continue;
        }
        if content[..start].chars().next_back().is_some_and(|before| {
            !is_python_whitespace(before)
                && !matches!(before, '\'' | '"' | '(' | '{' | '[' | '/' | '<')
        }) {
            continue;
        }
        let silent = content[start + 1..].starts_with('_');
        let marker_start = start + 1 + usize::from(silent);
        if !content[marker_start..].starts_with(stars) {
            continue;
        }
        let body_start = marker_start + stars.len();
        let Some(body_length) = content[body_start..].find('*') else {
            break;
        };
        let body_end = body_start + body_length;
        if body_length == 0 || !content[body_end..].starts_with(stars) {
            continue;
        }
        mention_end = body_end + stars.len();
        visit(start..mention_end, &content[body_start..body_end], silent);
    }
}

pub(crate) fn possible_user_mentions(content: &str) -> (HashSet<String>, bool, bool) {
    let mut names = HashSet::new();
    let mut topic_wildcard = false;
    let mut channel_wildcard = false;
    visit_mentions(content, "**", |_range, name, _silent| match name {
        "topic" => topic_wildcard = true,
        "all" | "everyone" | "stream" | "channel" => channel_wildcard = true,
        _ => {
            names.insert(name.to_owned());
        }
    });
    (names, topic_wildcard, channel_wildcard)
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
}
