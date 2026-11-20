use std::sync::OnceLock;

use regex::Regex;

pub use crate::markdown_autolink_data::UNICODE_VERSION;
use crate::markdown_autolink_data::WORD;

#[derive(Debug, PartialEq, Eq)]
pub struct TexMatch<'a> {
    pub before: &'a str,
    pub body: &'a str,
    pub last: Option<&'a str>,
    pub after: &'a str,
}

fn is_word(character: char) -> bool {
    static WORD_CHARACTER: OnceLock<Regex> = OnceLock::new();
    WORD_CHARACTER
        .get_or_init(|| Regex::new(&format!("[{WORD}]")).expect("generated Python word class"))
        .is_match(character.encode_utf8(&mut [0; 4]))
}

fn closes(data: &str, index: usize) -> bool {
    data[index..].starts_with("$$")
        && !data[index + 2..].starts_with('$')
        && !data[index + 2..].chars().next().is_some_and(is_word)
}

/// Match the legacy Pattern wrapper, including its final repeated capture.
/// Offsets used while scanning are UTF-8 byte boundaries.
pub fn find_tex(data: &str) -> Option<TexMatch<'_>> {
    for (start, _) in data.match_indices("$$") {
        if data[..start]
            .chars()
            .next_back()
            .is_some_and(|character| character == '$' || is_word(character))
        {
            continue;
        }
        let body_start = start + 2;
        let Some(first) = data[body_start..].chars().next() else {
            continue;
        };
        if matches!(first, '\n' | '_' | '$') {
            continue;
        }
        let mut index = body_start + first.len_utf8();
        let mut last = None;
        while let Some(character) = data[index..].chars().next() {
            if matches!(character, '$' | '\n') {
                break;
            }
            let end = index + character.len_utf8();
            let end = if character == '\\' && data[end..].starts_with('$') {
                end + 1
            } else {
                end
            };
            last = Some(&data[index..end]);
            index = end;
        }
        if !closes(data, index) {
            // The greedy escape branch can consume the first closing dollar.
            // Backtracking then consumes the backslash as an ordinary character.
            if last != Some("\\$") || !closes(data, index - 1) {
                continue;
            }
            index -= 1;
            last = Some("\\");
        }
        return Some(TexMatch {
            before: &data[..start],
            body: &data[body_start..index],
            last,
            after: &data[index + 2..],
        });
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_unicode_boundaries_and_captures() {
        assert_eq!(
            find_tex("📝 $$α_1$$ : $$later$$\n"),
            Some(TexMatch {
                before: "📝 ",
                body: "α_1",
                last: Some("1"),
                after: " : $$later$$\n",
            })
        );
        assert_eq!(find_tex("$$x$$").unwrap().last, None);
        for data in ["a$$x$$", "²$$x$$", "$$x$$سلام", "$$x$$²", "$$x$$_"] {
            assert_eq!(find_tex(data), None, "{data:?}");
        }
        for data in ["\u{301}$$x$$", "$$x$$\u{301}", "💬$$x$$💬"] {
            assert_eq!(find_tex(data).unwrap().body, "x", "{data:?}");
        }
    }

    #[test]
    fn preserves_escape_priority_and_backtracking() {
        for (data, body, last) in [
            (r"$$x\$$", "x\\", Some("\\")),
            (r"$$x\$$$", r"x\$", Some(r"\$")),
            (r"$$x\$y$$", r"x\$y", Some("y")),
            (r"$$\$$", "\\", None),
            (r"$$x\$$$$ : $$y$$", "y", None),
        ] {
            let matched = find_tex(data).unwrap();
            assert_eq!((matched.body, matched.last), (body, last), "{data:?}");
        }
    }

    #[test]
    fn rejects_invalid_delimiters_and_restarts_after_invalid_candidates() {
        for data in ["", "$$$$", "$$$x$$", "$$x$$$", "$$x\ny$$", "$$_x$$"] {
            assert_eq!(find_tex(data), None, "{data:?}");
        }
        assert_eq!(find_tex("$$bad$ : $$good$$").unwrap().body, "good");
        assert_eq!(find_tex("$$x\n$$y$$").unwrap().body, "y");
        assert_eq!(find_tex("$$ x $$").unwrap().body, " x ");
        assert_eq!(find_tex("$$\r$$").unwrap().body, "\r");
    }
}
