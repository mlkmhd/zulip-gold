//! Match the reference renderer's Unicode emoji grammar and presentation policy.

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::Regex;

pub use crate::markdown_emoji_data::REGEX_VERSION;
use crate::markdown_emoji_data::{EMOJI, MODIFIER, PRESENTATION, REGIONAL};

pub struct UnicodeEmoji {
    /// Version of the Python regex property tables used by the host renderer.
    pub regex_version: String,
    pub names: HashMap<String, String>,
}

pub(crate) struct Match {
    pub start: usize,
    pub end: usize,
    pub codepoint: String,
    pub syntax: String,
}

fn character_class(ranges: &[(u32, u32)]) -> String {
    let mut expression = "[".to_owned();
    for (start, end) in ranges {
        expression.push_str(&format!(r"\u{{{start:x}}}-\u{{{end:x}}}"));
    }
    expression.push(']');
    expression
}

static POSSIBLE_EMOJI: LazyLock<Regex> = LazyLock::new(|| {
    let regional = character_class(REGIONAL);
    let emoji = character_class(EMOJI);
    let modifier = character_class(MODIFIER);
    let emoji = format!(
        r"{emoji}(?:{modifier}|\u{{fe0f}}\u{{20e3}}?|[\u{{e0020}}-\u{{e007e}}]+\u{{e007f}})?"
    );
    let joined = format!(r"(?:{regional}{regional}|{emoji})");
    Regex::new(&format!(
        r"(?:{regional}{regional}|{emoji}(?:\u{{200d}}{joined})*)"
    ))
    .unwrap()
});

fn text_presentation(syntax: &str) -> bool {
    let mut chars = syntax.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    let point = u32::from(first);
    let index = PRESENTATION.partition_point(|(_, end)| *end < point);
    if PRESENTATION
        .get(index)
        .is_some_and(|(start, _)| *start <= point)
    {
        return false;
    }
    match chars.next() {
        None => true,
        Some('\u{20e3}') => chars.next().is_none(),
        _ => false,
    }
}

pub(crate) fn find(data: &str, catalog: &UnicodeEmoji) -> Option<Match> {
    for found in POSSIBLE_EMOJI.find_iter(data) {
        let syntax = found.as_str();
        if text_presentation(syntax) {
            continue;
        }
        let codepoint = syntax
            .chars()
            .filter(|c| *c != '\u{fe0f}')
            .map(|c| format!("{:04x}", u32::from(c)))
            .collect::<Vec<_>>()
            .join("-");
        if let Some(name) = catalog.names.get(&codepoint) {
            return Some(Match {
                start: found.start(),
                end: found.end(),
                codepoint,
                syntax: format!(":{name}:"),
            });
        }
    }
    None
}
