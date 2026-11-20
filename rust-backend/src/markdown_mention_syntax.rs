//! Locate user and group mention syntax using UTF-8 byte offsets.

use std::ops::Range;

use crate::draft_text::is_python_whitespace;

#[derive(Clone, Copy)]
pub enum Kind {
    User,
    Group,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Candidate {
    pub range: Range<usize>,
    pub name: Range<usize>,
    pub silent: bool,
}

#[derive(Debug, PartialEq, Eq)]
pub struct InvalidStart;

/// Find the first syntactic match, including its boundary in the original text.
///
/// The caller chooses where to resume after accepting or rejecting a candidate.
/// This does not resolve names, interpret code/quotes, or authorize a mention.
/// An invalid byte offset must delegate, rather than become a syntax miss.
pub fn find(data: &str, start: usize, kind: Kind) -> Result<Option<Candidate>, InvalidStart> {
    let remaining = data.get(start..).ok_or(InvalidStart)?;
    let stars = match kind {
        Kind::User => "**",
        Kind::Group => "*",
    };
    for (relative_start, _) in remaining.match_indices('@') {
        let start = start + relative_start;
        if data[..start].chars().next_back().is_some_and(|before| {
            !is_python_whitespace(before)
                && !matches!(before, '\'' | '"' | '(' | '{' | '[' | '/' | '<')
        }) {
            continue;
        }
        let silent = data[start + 1..].starts_with('_');
        let marker_start = start + 1 + usize::from(silent);
        if !data[marker_start..].starts_with(stars) {
            continue;
        }
        let body_start = marker_start + stars.len();
        let Some(body_length) = data[body_start..].find('*') else {
            break;
        };
        let body_end = body_start + body_length;
        if body_length == 0 || !data[body_end..].starts_with(stars) {
            continue;
        }
        return Ok(Some(Candidate {
            range: start..body_end + stars.len(),
            name: body_start..body_end,
            silent,
        }));
    }
    Ok(None)
}
