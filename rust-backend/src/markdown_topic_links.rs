//! Resolve raw URLs and linkifier matches in Zulip topic names.

use crate::markdown_autolinks::Matcher;
use crate::markdown_links::{clean_urlsplit_input, split};

const MAX_TOPIC_BYTES: usize = 16_384;
pub(crate) const MAX_CANDIDATES: usize = 4096;
const MATCH_BUDGET: usize = 1_000_000;

pub type Payload = (String, String);
pub type WireCandidate = (String, String, usize, Option<usize>);

struct Candidate {
    url: String,
    text: String,
    start: usize,
    end: usize,
    precedence: Option<usize>,
}

impl TryFrom<WireCandidate> for Candidate {
    type Error = ();

    fn try_from((url, text, start, precedence): WireCandidate) -> Result<Self, Self::Error> {
        let end = start.checked_add(text.chars().count()).ok_or(())?;
        Ok(Self {
            url,
            text,
            start,
            end,
            precedence,
        })
    }
}

fn make_url(raw: &str) -> Option<String> {
    let cleaned = clean_urlsplit_input(raw);
    let parts = split(&cleaned).ok()?;
    if !parts.scheme.is_empty() {
        return Some(raw.to_owned());
    }
    let (netloc, path) = if parts.netloc.is_empty() {
        let end = parts.path.find('/').unwrap_or(parts.path.len());
        parts.path.split_at(end)
    } else {
        (parts.netloc, parts.path)
    };
    if netloc.is_empty() {
        return None;
    }
    let mut url = format!("https://{netloc}{path}");
    if !parts.query.is_empty() {
        url.push('?');
        url.push_str(parts.query);
    }
    if !parts.fragment.is_empty() {
        url.push('#');
        url.push_str(parts.fragment);
    }
    Some(url)
}

pub fn raw_candidates(matcher: &Matcher, text: &str) -> Option<Vec<WireCandidate>> {
    if text.len() > MAX_TOPIC_BYTES {
        return None;
    }
    let mut remaining = MATCH_BUDGET;
    let mut index = 0;
    let mut candidates = Vec::new();
    for segment in text.split([' ', '!', ';', ')', ',', '\'', '"']) {
        if !segment.is_empty()
            && let Some(range) = matcher.find(segment, &mut remaining)?
        {
            let raw = &segment[range];
            // The reference places a raw candidate at the segment's start,
            // not at the start of the matched substring.
            candidates.push((make_url(raw)?, raw.to_owned(), index, None));
            if candidates.len() > MAX_CANDIDATES {
                return None;
            }
        }
        index += segment.chars().count() + 1;
    }
    Some(candidates)
}

fn starts_inside(current: &Candidate, previous: &Candidate) -> bool {
    previous.start <= current.start && current.start < previous.end
}

fn select_prepared(candidates: Vec<WireCandidate>) -> Option<Vec<(usize, Candidate)>> {
    if candidates.len() > MAX_CANDIDATES {
        return None;
    }
    let mut candidates = candidates
        .into_iter()
        .enumerate()
        .map(|(index, candidate)| Some((index, Candidate::try_from(candidate).ok()?)))
        .collect::<Option<Vec<_>>>()?;
    // Linkifiers are ordered by priority and position. Raw matches retain
    // their input scan order, and equal positions retain the original order.
    candidates.sort_by_key(|(_, candidate)| {
        (
            candidate.precedence.is_none(),
            candidate
                .precedence
                .map(|priority| (priority, candidate.start)),
        )
    });
    let mut applied = Vec::<(usize, Candidate)>::new();
    for (index, candidate) in candidates {
        if applied.iter().all(|(_, previous)| {
            !starts_inside(&candidate, previous) && !starts_inside(previous, &candidate)
        }) {
            applied.push((index, candidate));
        }
    }
    applied.sort_by_key(|(_, candidate)| candidate.start);
    Some(applied)
}

pub fn select(candidates: Vec<WireCandidate>) -> Option<Vec<Payload>> {
    Some(
        select_prepared(candidates)?
            .into_iter()
            .map(|(_, candidate)| (candidate.url, candidate.text))
            .collect(),
    )
}

#[cfg(any(feature = "python", test))]
pub(crate) fn select_indices(candidates: Vec<WireCandidate>) -> Option<Vec<usize>> {
    Some(
        select_prepared(candidates)?
            .into_iter()
            .map(|(index, _)| index)
            .collect(),
    )
}

#[cfg(test)]
#[test]
fn selects_original_offsets_without_recreating_payloads() {
    let candidates = vec![
        ("raw".into(), "🙂".into(), 4, None),
        ("linkifier".into(), "سلام".into(), 4, Some(1)),
        ("outside".into(), "x".into(), 0, Some(1)),
        ("same start and priority".into(), "X".into(), 4, Some(1)),
        ("later".into(), "z".into(), 8, None),
    ];
    assert_eq!(select_indices(candidates), Some(vec![2, 1, 4]));
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::markdown_autolinks::Spec;

    fn matcher() -> Matcher {
        Matcher::new(&Spec {
            tlds: vec!["com".into(), "org".into(), "test".into()],
            schemes: vec!["https?".into(), "obsidian".into()],
            enable_file_links: true,
        })
        .unwrap()
    }

    #[test]
    fn discovers_each_segment_and_uses_codepoint_indices() {
        assert_eq!(
            raw_candidates(
                &matcher(),
                "🙂(example.test/a?x=2#ref, http://example.com; plain example.org!",
            )
            .unwrap(),
            vec![
                (
                    "https://example.test/a?x=2#ref".into(),
                    "example.test/a?x=2#ref".into(),
                    0,
                    None,
                ),
                (
                    "http://example.com".into(),
                    "http://example.com".into(),
                    26,
                    None
                ),
                ("https://example.org".into(), "example.org".into(), 52, None),
            ],
        );
    }

    #[test]
    fn keeps_raw_email_and_file_conventions() {
        let inputs = "a@example.org file:///tmp/my-note.md";
        let selected = select(raw_candidates(&matcher(), inputs).unwrap()).unwrap();
        assert_eq!(
            selected,
            vec![
                ("https://a@example.org".into(), "a@example.org".into()),
                (
                    "file:///tmp/my-note.md".into(),
                    "file:///tmp/my-note.md".into()
                ),
            ],
        );
    }

    fn candidate(label: &str, text: &str, start: usize, priority: Option<usize>) -> WireCandidate {
        (label.into(), text.into(), start, priority)
    }

    #[test]
    fn priority_wins_over_position_and_preserves_stable_ties() {
        let prioritized = vec![
            candidate("linkifier-late", "س", 3, Some(0)),
            candidate("same", "س", 3, Some(0)),
            candidate("lower-priority", "3سa", 2, Some(1)),
            candidate("raw", "ABC", 2, None),
            candidate("raw-early", "🧪", 1, None),
        ];
        assert_eq!(
            select(prioritized).unwrap(),
            vec![
                ("raw-early".into(), "🧪".into()),
                ("linkifier-late".into(), "س".into())
            ],
        );
    }

    #[test]
    fn empty_matches_keep_original_point_overlap_semantics() {
        let candidates = vec![
            candidate("empty", "", 1, Some(0)),
            candidate("overlaps-empty", "xy", 1, Some(0)),
            candidate("also-empty", "", 1, Some(0)),
            candidate("after", "abc", 2, Some(0)),
        ];
        assert_eq!(
            select(candidates).unwrap(),
            vec![
                ("empty".into(), "".into()),
                ("also-empty".into(), "".into()),
                ("after".into(), "abc".into()),
            ],
        );
    }
}
