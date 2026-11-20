//! Locate channel, topic and message links using UTF-8 byte offsets.

use std::collections::HashSet;
use std::ops::Range;

use crate::draft_text::is_python_whitespace;

#[derive(Clone, Copy)]
pub enum Kind {
    Channel,
    Topic,
    Message,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Candidate {
    pub range: Range<usize>,
    pub channel: Range<usize>,
    pub topic: Option<Range<usize>>,
    pub message: Option<Range<usize>>,
}

#[derive(Debug, PartialEq, Eq)]
pub struct InvalidStart;

/// Find syntax without normalizing names or resolving channel access.
///
/// `unicode_decimal` must contain every non-ASCII decimal digit from the host's
/// Unicode database. It is used only for message links; ASCII digits are built
/// in. An invalid offset requires delegation, rather than a syntax miss.
/// The caller chooses where to resume after accepting or rejecting a candidate
/// and preserves the host registry's message, topic, then channel priority.
pub fn find(
    data: &str,
    start: usize,
    kind: Kind,
    unicode_decimal: &HashSet<char>,
) -> Result<Option<Candidate>, InvalidStart> {
    let remaining = data.get(start..).ok_or(InvalidStart)?;
    for (offset, _) in remaining.match_indices("#**") {
        let start = start + offset;
        if data[..start].chars().next_back().is_some_and(|before| {
            !is_python_whitespace(before) && !matches!(before, '\'' | '"' | '(' | '{' | '/' | '<')
        }) {
            continue;
        }
        let body_start = start + 3;
        let Some(length) = data[body_start..].find('*') else {
            break;
        };
        let body_end = body_start + length;
        if length == 0 || !data[body_end..].starts_with("**") {
            continue;
        }
        let mut candidate = Candidate {
            range: start..body_end + 2,
            channel: body_start..body_end,
            topic: None,
            message: None,
        };
        if matches!(kind, Kind::Topic | Kind::Message) {
            let Some(separator) = data[body_start..body_end].find('>') else {
                continue;
            };
            if separator == 0 {
                continue;
            }
            let channel_end = body_start + separator;
            candidate.channel.end = channel_end;
            let topic_start = channel_end + 1;
            let mut topic_end = body_end;
            if matches!(kind, Kind::Message) {
                let Some(separator) = data[topic_start..body_end].rfind('@') else {
                    continue;
                };
                topic_end = topic_start + separator;
                let message_start = topic_end + 1;
                if message_start == body_end
                    || !data[message_start..body_end]
                        .chars()
                        .all(|c| c.is_ascii_digit() || unicode_decimal.contains(&c))
                {
                    continue;
                }
                candidate.message = Some(message_start..body_end);
            }
            candidate.topic = Some(topic_start..topic_end);
        }
        return Ok(Some(candidate));
    }
    Ok(None)
}
