use std::collections::HashSet;

use crate::draft_text::is_python_whitespace;

pub(crate) type ChannelLinkReferences = (HashSet<String>, HashSet<(String, String)>);

pub(crate) fn possible_references(content: &str) -> ChannelLinkReferences {
    let mut channel_names = HashSet::new();
    let mut topics = HashSet::new();
    let mut channel_end = 0;
    let mut topic_end = 0;
    for (start, _) in content.match_indices("#**") {
        if start < channel_end && start < topic_end {
            continue;
        }
        if content[..start].chars().next_back().is_some_and(|before| {
            !is_python_whitespace(before) && !matches!(before, '\'' | '"' | '(' | '{' | '/' | '<')
        }) {
            continue;
        }
        let body_start = start + "#**".len();
        let Some(body_length) = content[body_start..].find('*') else {
            break;
        };
        let body_end = body_start + body_length;
        if body_length == 0 || !content[body_end..].starts_with("**") {
            continue;
        }
        let body = &content[body_start..body_end];
        if start >= channel_end {
            channel_names.insert(body.to_owned());
            channel_end = body_end + "**".len();
        }
        if start >= topic_end
            && let Some((channel, topic)) = body.split_once('>')
            && !channel.is_empty()
        {
            channel_names.insert(channel.to_owned());
            topics.insert((channel.to_owned(), topic.to_owned()));
            topic_end = body_end + "**".len();
        }
    }
    (channel_names, topics)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_channel_and_topic_candidates_without_normalizing_them() {
        let (channels, topics) = possible_references(
            "[#**hidden**] x#**hidden** \u{1f}#**Verona>topic@123** <#**Denmark>** \
             #**bad*channel** #**valid** #**ش\n channel>topic > more**",
        );
        assert_eq!(
            channels,
            [
                "Verona>topic@123",
                "Verona",
                "Denmark>",
                "Denmark",
                "valid",
                "ش\n channel>topic > more",
                "ش\n channel"
            ]
            .map(str::to_owned)
            .into()
        );
        assert_eq!(
            topics,
            [
                ("Verona", "topic@123"),
                ("Denmark", ""),
                ("ش\n channel", "topic > more")
            ]
            .map(|(channel, topic)| (channel.to_owned(), topic.to_owned()))
            .into()
        );
    }

    #[test]
    fn skips_empty_and_unclosed_candidates() {
        assert_eq!(
            possible_references("#**** #**>topic** #**unclosed"),
            ([">topic".to_owned()].into(), HashSet::new())
        );
    }

    #[test]
    fn tracks_the_channel_and_topic_patterns_independently() {
        assert_eq!(
            possible_references("#**prefix #**nested>topic**"),
            (
                ["prefix #", "nested"].map(str::to_owned).into(),
                [("nested".to_owned(), "topic".to_owned())].into()
            )
        );
    }
}
